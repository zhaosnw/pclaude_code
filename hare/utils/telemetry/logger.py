"""
OpenTelemetry DiagLogger bridge with real logging functionality.

Port of: src/utils/telemetry/logger.ts

Provides an OTEL-compatible diagnostic logger that routes all diagnostic
messages through Python's standard ``logging`` module. Supports:
  - Five OTEL diag levels: error, warn, info, debug, verbose
  - Configurable log level via ``CLAUDE_CODE_LOG_LEVEL`` env var
  - Structured (JSON) log entries for machine consumption
  - File-based logging with optional rotation
  - Singleton LogManager for application-wide configuration
  - Backward-compatible HareCodeDiagLogger class
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TextIO

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map OTEL diag severity names to Python logging levels
_DIAG_TO_PYTHON_LEVEL: dict[str, int] = {
    "error": logging.ERROR,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "verbose": 5,  # custom level below DEBUG
}

# Reverse mapping for display
_PYTHON_TO_DIAG_LEVEL: dict[int, str] = {
    logging.CRITICAL: "error",
    logging.ERROR: "error",
    logging.WARNING: "warn",
    logging.INFO: "info",
    logging.DEBUG: "debug",
    5: "verbose",
}

logging.addLevelName(5, "VERBOSE")

_LOG_LEVEL_ENV = "CLAUDE_CODE_LOG_LEVEL"
_LOG_FILE_ENV = "CLAUDE_CODE_LOG_FILE"
_LOG_JSON_ENV = "CLAUDE_CODE_LOG_JSON"
_DEBUG_ENV = "CLAUDE_CODE_DEBUG"

# Default log level when nothing is configured
_DEFAULT_LEVEL = logging.WARNING

# Name of the root application logger
_APP_LOGGER_NAME = "hare.telemetry"

# Maximum number of log records kept in the in-memory ring buffer
_MAX_RING_BUFFER_ENTRIES = 1024

# Default rate-limit window in seconds
_DEFAULT_RATE_LIMIT_WINDOW = 1.0
# Default max identical messages per window
_DEFAULT_RATE_LIMIT_MAX = 5

# ---------------------------------------------------------------------------
# Structured log record
# ---------------------------------------------------------------------------


@dataclass
class StructuredLogEntry:
    """A single structured telemetry log entry."""

    timestamp: str = ""
    level: str = "info"
    message: str = ""
    module: str = ""
    function: str = ""
    line: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    exc_info: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ts": self.timestamp,
            "level": self.level,
            "msg": self.message,
        }
        if self.module:
            d["module"] = self.module
        if self.function:
            d["func"] = self.function
        if self.line:
            d["line"] = self.line
        if self.metadata:
            d["meta"] = self.metadata
        if self.exc_info:
            d["exc"] = self.exc_info
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ---------------------------------------------------------------------------
# StructuredFormatter: JSON log formatter for external handlers
# ---------------------------------------------------------------------------


class StructuredFormatter(logging.Formatter):
    """``logging.Formatter`` that emits StructuredLogEntry JSON lines.

    Can be attached to any standard ``logging.Handler`` (file, stream,
    socket, SysLog) to produce machine-parseable telemetry output without
    requiring the custom ``_TelemetryHandler``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.converter = time.localtime

    def format(self, record: logging.LogRecord) -> str:
        diag_level = _PYTHON_TO_DIAG_LEVEL.get(record.levelno, "debug")
        entry = StructuredLogEntry(
            timestamp=time.strftime(
                "%Y-%m-%dT%H:%M:%S", self.converter(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            level=diag_level,
            message=record.getMessage(),
            module=record.module or "",
            function=record.funcName or "",
            line=record.lineno,
            metadata=getattr(record, "metadata", None) or {},
            exc_info=self.formatException(record.exc_info)
            if record.exc_info
            else None,
        )
        return entry.to_json()


# ---------------------------------------------------------------------------
# Custom handler: OTEL-aware JSON line output
# ---------------------------------------------------------------------------


class _TelemetryHandler(logging.Handler):
    """Logging handler that emits structured or human-readable telemetry lines."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        json_format: bool = False,
    ) -> None:
        super().__init__()
        self.stream = stream or sys.stderr
        self._json = json_format

    def emit(self, record: logging.LogRecord) -> None:
        try:
            diag_level = _PYTHON_TO_DIAG_LEVEL.get(record.levelno, "debug")
            if self._json:
                entry = StructuredLogEntry(
                    timestamp=time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
                    )
                    + f".{int(record.msecs):03d}Z",
                    level=diag_level,
                    message=record.getMessage(),
                    module=record.module or "",
                    function=record.funcName or "",
                    line=record.lineno,
                    metadata=getattr(record, "metadata", {}) or {},
                    exc_info=self.formatter.formatException(record.exc_info)
                    if record.exc_info
                    else None,
                )
                self.stream.write(entry.to_json() + "\n")
                self.stream.flush()
            else:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
                msg = self.format(record)
                self.stream.write(f"[{ts}] [{diag_level.upper():>7s}] {msg}\n")
                self.stream.flush()
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# RateLimitingFilter — suppress duplicate log storms
# ---------------------------------------------------------------------------


class RateLimitingFilter(logging.Filter):
    """Suppress duplicate log messages that arrive faster than a threshold.

    When the same ``(level, message)`` pair is seen more than *max_count*
    times within *window_seconds*, subsequent copies are dropped. A summary
    line (``"suppressed N duplicates"``) is emitted once the window rolls
    forward.

    Attach to any logger or handler::

        logger.addFilter(RateLimitingFilter(window_seconds=2.0, max_count=5))
    """

    def __init__(
        self,
        window_seconds: float = _DEFAULT_RATE_LIMIT_WINDOW,
        max_count: int = _DEFAULT_RATE_LIMIT_MAX,
    ) -> None:
        super().__init__()
        self._window = window_seconds
        self._max_count = max_count
        self._buckets: dict[tuple[int, str], tuple[float, int, bool]] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        import time as _time

        now = _time.monotonic()
        key = (record.levelno, record.getMessage())

        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = (now, 1, False)
            # Purge old entries periodically
            if len(self._buckets) > 512:
                self._purge(now)
            return True

        start, count, suppressed = bucket
        if now - start > self._window:
            # Window expired — emit suppressed count if any were dropped
            if suppressed:
                record.msg = (
                    f"[suppressed {count - self._max_count} duplicates] "
                    f"{record.msg}"
                )
                self._buckets[key] = (now, 1, False)
                return True
            else:
                self._buckets[key] = (now, 1, False)
                return True

        if count < self._max_count:
            self._buckets[key] = (start, count + 1, False)
            return True

        self._buckets[key] = (start, count + 1, True)
        return False

    def _purge(self, now: float) -> None:
        stale = [
            k
            for k, (start, _, _) in self._buckets.items()
            if now - start > self._window * 2
        ]
        for k in stale:
            del self._buckets[k]


# ---------------------------------------------------------------------------
# LogManager — singleton configuration hub
# ---------------------------------------------------------------------------


class LogManager:
    """Central configuration point for telemetry logging.

    Thread-safe singleton that configures the Python ``logging`` hierarchy
    used by all telemetry loggers and the ``HareCodeDiagLogger`` bridge.

    Usage::

        mgr = LogManager.get_instance()
        mgr.set_level("debug")
        mgr.enable_file_logging("/tmp/hare-telemetry.log")
        mgr.enable_json_format()
    """

    _instance: LogManager | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._root_logger = logging.getLogger(_APP_LOGGER_NAME)
        self._root_logger.setLevel(_DEFAULT_LEVEL)
        self._root_logger.propagate = False
        self._stderr_handler: _TelemetryHandler | None = None
        self._file_handler: logging.FileHandler | None = None
        self._json_enabled: bool = False
        self._initialized: bool = False
        self._pending_logs: list[logging.LogRecord] = []
        self._init_from_env()

    # ---- singleton -----------------------------------------------------------

    @classmethod
    def get_instance(cls) -> LogManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Teardown existing singleton (mainly for tests)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance._shutdown()
                cls._instance = None

    # ---- init ----------------------------------------------------------------

    def _init_from_env(self) -> None:
        """Read env vars and configure settings only — does NOT activate handlers.

        Handlers are deferred until ``init_complete()`` is called so that
        callers have a chance to register sinks, filters, and custom
        handlers before any output is emitted.
        """
        level_name = os.environ.get(_LOG_LEVEL_ENV, "").lower()
        if level_name:
            self.set_level(level_name)

        if os.environ.get(_DEBUG_ENV) == "1":
            self.set_level("debug")

        if os.environ.get(_LOG_JSON_ENV) == "1":
            self.enable_json_format()

        log_file = os.environ.get(_LOG_FILE_ENV)
        if log_file:
            self.enable_file_logging(log_file)

    def _ensure_stderr_handler(self) -> None:
        if self._stderr_handler is None:
            self._stderr_handler = _TelemetryHandler(stream=sys.stderr, json_format=self._json_enabled)
            self._stderr_handler.setLevel(logging.DEBUG)
            self._root_logger.addHandler(self._stderr_handler)

    def _shutdown(self) -> None:
        for handler in list(self._root_logger.handlers):
            handler.close()
            self._root_logger.removeHandler(handler)
        self._stderr_handler = None
        self._file_handler = None

    # ---- level management ----------------------------------------------------

    def set_level(self, level: str | int) -> None:
        """Set the minimum severity for telemetry logging.

        ``level`` accepts OTEL diag strings (``"error"`` through
        ``"verbose"``) or Python ``logging`` integer constants.
        """
        if isinstance(level, str):
            py_level = _DIAG_TO_PYTHON_LEVEL.get(level.lower())
            if py_level is None:
                py_level = _DEFAULT_LEVEL
        else:
            py_level = level
        self._root_logger.setLevel(py_level)

    def get_level(self) -> str:
        """Return the current minimum level as a diag string."""
        return _PYTHON_TO_DIAG_LEVEL.get(self._root_logger.level, "info")

    # ---- destinations --------------------------------------------------------

    def enable_file_logging(
        self,
        path: str,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        """Write telemetry logs to *path* with optional rotation."""
        if self._file_handler is not None:
            self._root_logger.removeHandler(self._file_handler)
            self._file_handler.close()

        from logging.handlers import RotatingFileHandler

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._file_handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        self._file_handler.setLevel(logging.DEBUG)
        self._file_handler.setFormatter(logging.Formatter("%(message)s"))
        self._root_logger.addHandler(self._file_handler)
        self.debug("LogManager", f"File logging enabled: {path}")

    def disable_file_logging(self) -> None:
        if self._file_handler is not None:
            self._root_logger.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

    def enable_json_format(self) -> None:
        self._json_enabled = True
        if self._stderr_handler is not None:
            self._stderr_handler._json = True

    def disable_json_format(self) -> None:
        self._json_enabled = False
        if self._stderr_handler is not None:
            self._stderr_handler._json = False

    # ---- low-level emit ------------------------------------------------------

    def _emit(
        self,
        level: int,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
        exc_info: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(extra or {})
        if metadata:
            merged.setdefault("metadata", {}).update(metadata)
        if not self._initialized:
            record = self._root_logger.makeRecord(
                self._root_logger.name,
                level,
                "(unknown)",
                0,
                message,
                args=(),
                exc_info=None,
            )
            record.__dict__.update(merged)
            self._pending_logs.append(record)
            return
        self._root_logger.log(level, message, exc_info=exc_info, extra=merged)

    # ---- convenience helpers -------------------------------------------------

    def error(
        self,
        source: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
        exc_info: bool = False,
    ) -> None:
        self._emit(
            logging.ERROR,
            f"[{source}] {message}",
            metadata=metadata,
            exc_info=exc_info,
            extra={"diag_source": source},
        )

    def warn(
        self,
        source: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            logging.WARNING,
            f"[{source}] {message}",
            metadata=metadata,
            extra={"diag_source": source},
        )

    def info(
        self,
        source: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            logging.INFO,
            f"[{source}] {message}",
            metadata=metadata,
            extra={"diag_source": source},
        )

    def debug(
        self,
        source: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            logging.DEBUG,
            f"[{source}] {message}",
            metadata=metadata,
            extra={"diag_source": source},
        )

    def verbose(
        self,
        source: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            5,
            f"[{source}] {message}",
            metadata=metadata,
            extra={"diag_source": source},
        )

    # ---- public API ----------------------------------------------------------

    def get_root_logger(self) -> logging.Logger:
        return self._root_logger

    def create_diag_logger(self) -> HareCodeDiagLogger:
        """Return an OTEL-compatible diag logger bound to this manager."""
        return HareCodeDiagLogger(self)

    # ---- buffered initialization --------------------------------------------

    def _flush_pending_logs(self) -> None:
        """Drain any log records buffered before full initialization."""
        if not self._pending_logs:
            return
        self.debug("LogManager", f"Flushing {len(self._pending_logs)} buffered logs")
        for record in self._pending_logs:
            if not self._root_logger.isEnabledFor(record.levelno):
                continue
            self._root_logger.handle(record)
        self._pending_logs.clear()

    def init_complete(self) -> None:
        """Mark LogManager as fully initialized and flush buffered logs.

        Call this once after all configuration (sinks, handlers, filters)
        has been registered. Until called, log records are buffered in
        ``_pending_logs`` without being written.
        """
        if self._initialized:
            return
        self._initialized = True
        self._ensure_stderr_handler()
        self._flush_pending_logs()


# ---------------------------------------------------------------------------
# LogRingBuffer — in-memory circular buffer for recent logs
# ---------------------------------------------------------------------------


class LogRingBuffer:
    """Circular buffer that retains the last *capacity* log records in memory.

    Useful for crash debugging: dump the buffer on a fatal exception to get
    the log context leading up to the failure without enabling debug logging
    permanently.

    Usage::

        buf = LogRingBuffer(256)
        LogManager.get_instance().get_root_logger().addHandler(buf)
        ...
        for entry in buf.recent():
            print(entry)
    """

    def __init__(self, capacity: int = _MAX_RING_BUFFER_ENTRIES) -> None:
        self._capacity = max(1, capacity)
        self._buffer: list[StructuredLogEntry] = []
        self._pos = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = StructuredLogEntry(
            timestamp=time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            level=_PYTHON_TO_DIAG_LEVEL.get(record.levelno, "debug"),
            message=record.getMessage(),
            module=record.module or "",
            function=record.funcName or "",
            line=record.lineno,
            metadata=getattr(record, "metadata", None) or {},
            exc_info=None,
        )
        with self._lock:
            if len(self._buffer) < self._capacity:
                self._buffer.append(entry)
            else:
                self._buffer[self._pos] = entry
                self._pos = (self._pos + 1) % self._capacity

    def handle(self, record: logging.LogRecord) -> bool:
        """logging.Handler-compatible interface."""
        self.emit(record)
        return True

    def recent(self, count: int | None = None) -> list[StructuredLogEntry]:
        """Return the most recent log records (newest last).

        If *count* is ``None``, returns everything in the buffer.
        """
        with self._lock:
            if len(self._buffer) < self._capacity:
                entries = list(self._buffer)
            else:
                entries = (
                    self._buffer[self._pos :] + self._buffer[: self._pos]
                )
            if count is not None and count < len(entries):
                return entries[-count:]
            return entries

    def filter_by_level(self, min_level: str) -> list[StructuredLogEntry]:
        """Return recent entries at or above *min_level* (e.g. ``"warn"``)."""
        threshold = _DIAG_TO_PYTHON_LEVEL.get(
            min_level, logging.DEBUG
        )
        return [
            e
            for e in self.recent()
            if _DIAG_TO_PYTHON_LEVEL.get(e.level, logging.DEBUG) >= threshold
        ]

    def to_text(self, count: int | None = None) -> str:
        """Render the most recent entries as human-readable lines."""
        lines: list[str] = []
        for e in self.recent(count):
            header = f"[{e.timestamp}] [{e.level.upper():>7s}] {e.message}"
            if e.exc_info:
                header += f"\n{e.exc_info}"
            lines.append(header)
        return "\n".join(lines)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._pos = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


# ---------------------------------------------------------------------------
# Logging sinks registry
# ---------------------------------------------------------------------------

_log_sinks: list[Callable[[str, str, Any], None]] = []


def register_log_sink(sink: Callable[[str, str, Any], None]) -> None:
    """Register a custom sink that receives (level, message, metadata).

    Sinks are called for every log message emitted by ``HareCodeDiagLogger``
    and ``LogManager``, enabling integration with analytics pipelines,
    error tracking services, or custom telemetry backends.
    """
    if sink not in _log_sinks:
        _log_sinks.append(sink)


def unregister_log_sink(sink: Callable[[str, str, Any], None]) -> None:
    if sink in _log_sinks:
        _log_sinks.remove(sink)


def clear_log_sinks() -> None:
    _log_sinks.clear()


# ---------------------------------------------------------------------------
# HareCodeDiagLogger — OTEL DiagLogger interface
# ---------------------------------------------------------------------------


class HareCodeDiagLogger:
    """Maps OTEL diag callbacks to application logging.

    Implements the OpenTelemetry ``DiagLogger`` interface. Every call is
    forwarded to the singleton ``LogManager`` and any registered log sinks.

    Usage with OpenTelemetry SDK::

        from opentelemetry.diag import set_logger

        set_logger(HareCodeDiagLogger())
    """

    def __init__(self, manager: LogManager | None = None) -> None:
        self._manager = manager or LogManager.get_instance()

    # ---- DiagLogger interface ------------------------------------------------

    def error(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._manager.error("otel", formatted)
        for sink in _log_sinks:
            try:
                sink("error", formatted, None)
            except Exception:
                pass

    def warn(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._manager.warn("otel", formatted)
        for sink in _log_sinks:
            try:
                sink("warn", formatted, None)
            except Exception:
                pass

    def info(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._manager.info("otel", formatted)
        for sink in _log_sinks:
            try:
                sink("info", formatted, None)
            except Exception:
                pass

    def debug(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._manager.debug("otel", formatted)
        for sink in _log_sinks:
            try:
                sink("debug", formatted, None)
            except Exception:
                pass

    def verbose(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._manager.verbose("otel", formatted)
        for sink in _log_sinks:
            try:
                sink("verbose", formatted, None)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# ScopedLogger — contextual logger with automatic source prefixing
# ---------------------------------------------------------------------------


class ScopedLogger:
    """A logger that auto-prefixes every message with a source scope.

    Wraps a ``HareCodeDiagLogger`` (or any compatible object) and prepends
    ``[scope]`` to each log message, making it easy to identify which
    subsystem produced a given log line without manually formatting every
    call site.

    Supports nested scopes via the ``sub`` method, producing prefix chains
    like ``[main][worker][transport] message``.

    Usage::

        log = ScopedLogger("transport")
        log.info("connected")  # emits: [transport] connected

        sub = log.sub("websocket")
        sub.debug("ping")      # emits: [transport][websocket] ping
    """

    __slots__ = ("_diag", "_scope", "_manager")

    def __init__(
        self,
        scope: str,
        *,
        parent: ScopedLogger | None = None,
        diag: HareCodeDiagLogger | None = None,
    ) -> None:
        if parent is not None:
            self._diag = parent._diag
            self._scope = f"{parent._scope}[{scope}]"
            self._manager = parent._manager
        else:
            self._diag = diag or HareCodeDiagLogger()
            self._scope = f"[{scope}]"
            self._manager = self._diag._manager

    def sub(self, scope: str) -> ScopedLogger:
        """Return a child logger with an additional nested scope."""
        return ScopedLogger(scope, parent=self)

    def _fmt(self, message: str) -> str:
        return f"{self._scope} {message}"

    def error(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._diag.error(self._fmt(formatted))

    def warn(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._diag.warn(self._fmt(formatted))

    def info(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._diag.info(self._fmt(formatted))

    def debug(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._diag.debug(self._fmt(formatted))

    def verbose(self, message: str, *args: object) -> None:
        formatted = message % args if args else message
        self._diag.verbose(self._fmt(formatted))

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def manager(self) -> LogManager:
        return self._manager


# ---------------------------------------------------------------------------
# LogContext — async-safe scoped metadata propagation
# ---------------------------------------------------------------------------

import contextvars as _contextvars  # noqa: E402 (intentional late import)

_log_context_var: _contextvars.ContextVar[dict[str, Any]] = _contextvars.ContextVar(
    "hare_telemetry_context", default={}
)


class LogContext:
    """Async-safe structured metadata propagated through call chains.

    Uses a ``contextvars.ContextVar`` so metadata survives ``asyncio`` task
    switches without explicit parameter threading.  Any log record emitted
    while a context is active receives the context metadata automatically.

    Typical production patterns::

        # Set per-request context in middleware
        token = LogContext.set(request_id=str(uuid4()), session_id=sid)
        ...
        # Every log message inside this scope includes the metadata
        LogContext.reset(token)

        # Context manager form
        with LogContext.context(request_id="req-42"):
            get_logger("handler").info("processing order")  # carries request_id

        # Read current context (e.g. for custom sinks)
        ctx = LogContext.current()
    """

    _TOKEN = object  # sentinel for "no token"

    @staticmethod
    def current() -> dict[str, Any]:
        """Return a copy of the current context metadata dict (never ``None``)."""
        return dict(_log_context_var.get())

    @staticmethod
    def set(**metadata: Any) -> _contextvars.Token:
        """Merge *metadata* into the current context, returning a reset token.

        Returns a ``contextvars.Token`` that can be passed to ``reset()``
        to restore the previous state.
        """
        current = dict(_log_context_var.get())
        current.update(metadata)
        return _log_context_var.set(current)

    @staticmethod
    def reset(token: _contextvars.Token) -> None:
        """Restore the context to the state captured by *token*."""
        _log_context_var.reset(token)

    @staticmethod
    def context(**metadata: Any):
        """Context manager that enters/cleans up metadata automatically.

        Usage::

            with LogContext.context(request_id="abc", operation="sync"):
                ...
        """
        return _LogContextScope(metadata)

    @staticmethod
    def _enrich_record(record: dict[str, Any]) -> None:
        """Inject current context metadata into *record* (called by sinks)."""
        ctx = _log_context_var.get()
        if ctx:
            record.setdefault("meta", {}).update(ctx)


class _LogContextScope:
    """Internal context-manager implementation for ``LogContext.context()``."""

    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata
        self._token: _contextvars.Token | None = None

    def __enter__(self) -> None:
        self._token = LogContext.set(**self._metadata)

    def __exit__(self, *args: object) -> None:
        if self._token is not None:
            LogContext.reset(self._token)


# ---------------------------------------------------------------------------
# TimedOperation — entry/exit/duration logging context manager
# ---------------------------------------------------------------------------


class TimedOperation:
    """Context manager that logs entry, exit, and wall-clock duration.

    On ``__enter__`` the operation name and optional metadata are logged at
    *enter_level* (default ``"debug"``). On ``__exit__`` the duration is
    logged at *exit_level* (default ``"debug"``). If an exception propagates
    through the context, it is logged at ``"error"`` level with the stack
    trace and re-raised.

    Usage::

        with TimedOperation("db.query", table="users"):
            results = db.execute(sql)

        # Emits:
        #   [db.query] start (table=users)
        #   [db.query] completed in 42.3ms
    """

    def __init__(
        self,
        name: str,
        *,
        logger: HareCodeDiagLogger | None = None,
        enter_level: str = "debug",
        exit_level: str = "debug",
        **metadata: Any,
    ) -> None:
        self._name = name
        self._log = logger or get_logger()
        self._enter_level = enter_level
        self._exit_level = exit_level
        self._metadata = metadata
        self._start: float = 0.0
        self._error: Exception | None = None

    def __enter__(self) -> TimedOperation:
        self._start = time.monotonic()
        meta_str = " ".join(f"{k}={v}" for k, v in self._metadata.items())
        msg = f"[{self._name}] start"
        if meta_str:
            msg += f" ({meta_str})"
        _dispatch_level(self._log, self._enter_level, msg)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        elapsed_ms = (time.monotonic() - self._start) * 1000
        if exc_type is not None:
            self._log.error(
                f"[{self._name}] failed after {elapsed_ms:.1f}ms: {exc_val}"
            )
            return False  # re-raise
        self._log.debug(f"[{self._name}] completed in {elapsed_ms:.1f}ms")
        return False


def _dispatch_level(logger: HareCodeDiagLogger, level: str, message: str) -> None:
    """Route *message* to the appropriate logger method by level name."""
    method = getattr(logger, level, None)
    if callable(method):
        method(message)
    else:
        logger.debug(message)


# ---------------------------------------------------------------------------
# OperationStats — accumulate and report operation timing statistics
# ---------------------------------------------------------------------------


class OperationStats:
    """Online statistics tracker for operation durations.

    Accepts duration samples via ``record()`` and can periodically dump a
    summary to the telemetry logger.  Computes count, min, max, mean, and
    approximate percentiles (p50, p95, p99) using a simple reservoir.

    Usage::

        stats = OperationStats("rpc.latency", log_interval=100)
        for _ in range(200):
            with TimedOperation("call") as op:
                do_work()
            stats.record(op.elapsed_ms())  # or record manually
        stats.flush()  # logs: [rpc.latency] n=200 min=0.1 max=45.2 mean=5.3 p50=4.1 p95=32.0
    """

    def __init__(
        self,
        name: str,
        *,
        log_interval: int = 0,
        logger: HareCodeDiagLogger | None = None,
        max_samples: int = 4096,
    ) -> None:
        self._name = name
        self._log = logger or get_logger()
        self._log_interval = log_interval
        self._max_samples = max_samples
        self._count: int = 0
        self._min: float = float("inf")
        self._max: float = float("-inf")
        self._sum: float = 0.0
        self._samples: list[float] = []
        self._lock = threading.RLock()

    def record(self, duration_ms: float) -> None:
        """Feed a single duration sample (milliseconds)."""
        with self._lock:
            self._count += 1
            self._sum += duration_ms
            if duration_ms < self._min:
                self._min = duration_ms
            if duration_ms > self._max:
                self._max = duration_ms
            if len(self._samples) < self._max_samples:
                self._samples.append(duration_ms)
            else:
                # Reservoir sampling: replace a random element
                import random

                idx = random.randint(0, self._count - 1)
                if idx < self._max_samples:
                    self._samples[idx] = duration_ms
            if self._log_interval > 0 and self._count % self._log_interval == 0:
                self.flush()

    def flush(self) -> None:
        """Log a summary snapshot and reset counters."""
        with self._lock:
            if self._count == 0:
                return
            mean = self._sum / self._count
            parts = [
                f"n={self._count}",
                f"min={self._min:.2f}",
                f"max={self._max:.2f}",
                f"mean={mean:.2f}",
            ]
            if self._samples:
                sorted_samples = sorted(self._samples)
                p50 = _percentile(sorted_samples, 50)
                p95 = _percentile(sorted_samples, 95)
                p99 = _percentile(sorted_samples, 99)
                parts.extend(
                    [f"p50={p50:.2f}", f"p95={p95:.2f}", f"p99={p99:.2f}"]
                )
            self._log.info(f"[{self._name}] {' '.join(parts)}")
            # Reset
            self._count = 0
            self._min = float("inf")
            self._max = float("-inf")
            self._sum = 0.0
            self._samples.clear()

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def stats(self) -> dict[str, Any]:
        """Return a snapshot dict without resetting."""
        with self._lock:
            if self._count == 0:
                return {"count": 0}
            mean = self._sum / self._count
            result: dict[str, Any] = {
                "count": self._count,
                "min": self._min,
                "max": self._max,
                "mean": mean,
            }
            if self._samples:
                s = sorted(self._samples)
                result["p50"] = _percentile(s, 50)
                result["p95"] = _percentile(s, 95)
                result["p99"] = _percentile(s, 99)
            return result


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Return the *pct*-th percentile from a sorted list (nearest-rank method)."""
    if not sorted_vals:
        return 0.0
    idx = int(round((pct / 100.0) * (len(sorted_vals) - 1)))
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


# ---------------------------------------------------------------------------
# CaptureLogs — test utility for capturing log records in-memory
# ---------------------------------------------------------------------------


class CaptureLogs:
    """Context manager that captures telemetry log records into a list.

    Attaches a temporary handler to the root telemetry logger so that every
    log record emitted inside the ``with`` block is appended to
    ``records``.  Restores the original handlers on exit.

    Usage::

        with CaptureLogs() as captured:
            get_logger("test").error("boom", "detail")
            get_logger("test").info("all good")

        assert any("boom" in r.message for r in captured.records)
        assert captured.has_message_containing("all good")
        assert captured.count == 2
    """

    def __init__(self, level: int = logging.DEBUG) -> None:
        self._level = level
        self._root = LogManager.get_instance().get_root_logger()
        self._handler: logging.Handler | None = None
        self.records: list[StructuredLogEntry] = []

    def __enter__(self) -> CaptureLogs:
        self._handler = _CaptureHandler(self.records)
        self._handler.setLevel(self._level)
        self._root.addHandler(self._handler)
        return self

    def __exit__(self, *args: object) -> None:
        if self._handler is not None:
            self._root.removeHandler(self._handler)
            self._handler = None

    @property
    def count(self) -> int:
        return len(self.records)

    def by_level(self, level: str) -> list[StructuredLogEntry]:
        return [r for r in self.records if r.level == level]

    def has_message_containing(self, substring: str) -> bool:
        return any(substring in r.message for r in self.records)

    def has_message_matching(self, pattern: str) -> bool:
        import re

        return any(re.search(pattern, r.message) for r in self.records)

    def messages(self) -> list[str]:
        return [r.message for r in self.records]


class _CaptureHandler(logging.Handler):
    """Minimal handler that converts LogRecords to StructuredLogEntry."""

    def __init__(self, target: list[StructuredLogEntry]) -> None:
        super().__init__()
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        entry = StructuredLogEntry(
            timestamp=time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            level=_PYTHON_TO_DIAG_LEVEL.get(record.levelno, "debug"),
            message=record.getMessage(),
            module=record.module or "",
            function=record.funcName or "",
            line=record.lineno,
            metadata=getattr(record, "metadata", None) or {},
            exc_info=None,
        )
        self._target.append(entry)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def get_logger(name: str | None = None) -> HareCodeDiagLogger:
    """Create (or retrieve) a diag logger with the given *name*.

    When *name* is ``None`` a logger bound to the root telemetry namespace
    is returned. Named loggers are shared within the process — repeated
    calls with the same name return the same instance.
    """
    if name is None:
        return _global_logger
    if name not in _named_loggers:
        _named_loggers[name] = HareCodeDiagLogger()
    return _named_loggers[name]


def set_level(level: str) -> None:
    """Convenience to set the global telemetry log level."""
    LogManager.get_instance().set_level(level)


def get_level() -> str:
    """Convenience to read the current global telemetry log level."""
    return LogManager.get_instance().get_level()


_global_logger = HareCodeDiagLogger()
_named_loggers: dict[str, HareCodeDiagLogger] = {}


def error(message: str, *args: object) -> None:
    _global_logger.error(message, *args)


def warn(message: str, *args: object) -> None:
    _global_logger.warn(message, *args)


def info(message: str, *args: object) -> None:
    _global_logger.info(message, *args)


def debug(message: str, *args: object) -> None:
    _global_logger.debug(message, *args)


def verbose(message: str, *args: object) -> None:
    _global_logger.verbose(message, *args)
