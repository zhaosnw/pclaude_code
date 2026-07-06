"""
Process and manage Python warnings with suppression and structured display.

Port of: src/utils/warningHandler.ts

Provides a central warning management system that integrates with the Hare
telemetry logger and supports:

- **Suppression**: filter warnings by category, message pattern, module, or key.
  Uses a bounded key ring (MAX_WARNING_KEYS) to avoid unbounded memory growth.
- **Display**: renders warnings through a configurable formatter that can emit
  human-readable or structured (JSON) output via the telemetry ``LogManager``.
- **Configuration**: respects ``CLAUDE_CODE_WARNING_LEVEL``, ``CLAUDE_CODE_QUIET``,
  and ``CLAUDE_CODE_DEBUG`` environment variables and settings.

Usage::

    from hare.utils.warning_handler import (
        initialize_warning_handler,
        suppress_warning_by_key,
        unsuppress_warning_by_key,
        suppress_warnings_from_module,
        display_warning,
    )

    # At startup:
    initialize_warning_handler()

    # Suppress a specific warning seen before:
    suppress_warning_by_key("deprecated-api-v1")

    # Display a programmatic warning:
    display_warning("Configuration issue: API key not found", category="auth")
"""

from __future__ import annotations

import logging
import os
import re
import threading
import traceback
import warnings
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_WARNING_KEYS = 1000
DEFAULT_COOLDOWN_SECONDS = 60.0
MAX_HISTORY_ENTRIES = 500

_WARNING_LEVEL_ENV = "CLAUDE_CODE_WARNING_LEVEL"
_QUIET_ENV = "CLAUDE_CODE_QUIET"
_DEBUG_ENV = "CLAUDE_CODE_DEBUG"


# ---------------------------------------------------------------------------
# Warning severity
# ---------------------------------------------------------------------------


class WarningSeverity(Enum):
    """Severity levels mapped from Python's logging/warnings scale."""

    IGNORE = auto()
    ONCE = auto()
    ALWAYS = auto()
    ERROR = auto()

    @classmethod
    def from_string(cls, s: str) -> WarningSeverity:
        mapping = {
            "ignore": cls.IGNORE,
            "once": cls.ONCE,
            "always": cls.ALWAYS,
            "default": cls.ONCE,
            "module": cls.ONCE,
            "error": cls.ERROR,
        }
        return mapping.get(s.lower(), cls.ALWAYS)


# ---------------------------------------------------------------------------
# Warning record
# ---------------------------------------------------------------------------


@dataclass
class WarningRecord:
    """Structured representation of a captured warning."""

    message: str
    category: str
    filename: str
    lineno: int
    line: str | None = None
    key: str = ""
    count: int = 1
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.key:
            self.key = self._make_key()

    def _make_key(self) -> str:
        """Build a stable deduplication key from message, category, and location."""
        # Normalize whitespace in message for stable keys
        normalized = re.sub(r"\s+", " ", self.message).strip()
        return f"{normalized}::{self.category}::{self.filename}:{self.lineno}"

    def matches_pattern(self, pattern: str | re.Pattern) -> bool:
        """Check if this warning's message matches *pattern*."""
        if isinstance(pattern, re.Pattern):
            return bool(pattern.search(self.message))
        return pattern.lower() in self.message.lower()

    def matches_module(self, module_glob: str) -> bool:
        """Check if the warning originated from a file matching *module_glob*.

        ``module_glob`` is a glob-like pattern, e.g. ``"hare/utils/*"``.
        """
        return re.match(
            module_glob.replace(".", r"\.").replace("*", ".*").replace("?", "."),
            self.filename,
        ) is not None


# ---------------------------------------------------------------------------
# Warning history tracker
# ---------------------------------------------------------------------------


class WarningHistory:
    """Collects warning statistics for diagnostics and reporting.

    Tracks per-key and per-category counts, first/last seen timestamps,
    and total warning volume. Bounded to ``MAX_HISTORY_ENTRIES``.
    """

    def __init__(self, max_entries: int = MAX_HISTORY_ENTRIES) -> None:
        self._max_entries = max_entries
        self._by_key: dict[str, dict[str, Any]] = {}
        self._by_category: dict[str, int] = {}
        self._total_warnings: int = 0
        self._total_suppressed: int = 0
        self._lock = threading.RLock()

    def record_seen(self, record: WarningRecord) -> None:
        """Log that a warning was displayed."""
        with self._lock:
            self._total_warnings += 1
            self._by_category[record.category] = (
                self._by_category.get(record.category, 0) + 1
            )
            entry = self._by_key.get(record.key)
            if entry is None:
                if len(self._by_key) >= self._max_entries:
                    oldest = min(self._by_key, key=lambda k: self._by_key[k]["first"])
                    del self._by_key[oldest]
                import time

                self._by_key[record.key] = {
                    "first": time.time(),
                    "last": time.time(),
                    "count": 1,
                    "category": record.category,
                    "message": record.message,
                }
            else:
                entry["count"] += 1
                import time

                entry["last"] = time.time()

    def record_suppressed(self, record: WarningRecord) -> None:
        """Log that a warning was suppressed."""
        with self._lock:
            self._total_suppressed += 1

    @property
    def total_warnings(self) -> int:
        with self._lock:
            return self._total_warnings

    @property
    def total_suppressed(self) -> int:
        with self._lock:
            return self._total_suppressed

    def top_categories(self, n: int = 10) -> list[tuple[str, int]]:
        """Return the top *n* categories by warning count."""
        with self._lock:
            sorted_cats = sorted(
                self._by_category.items(), key=lambda x: x[1], reverse=True
            )
            return sorted_cats[:n]

    def top_keys(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the top *n* warning keys by count."""
        with self._lock:
            sorted_keys = sorted(
                self._by_key.values(), key=lambda x: x["count"], reverse=True
            )
            return sorted_keys[:n]

    def get_summary(self) -> dict[str, Any]:
        """Return a JSON-serializable warning summary suitable for diagnostics."""
        with self._lock:
            return {
                "total_warnings": self._total_warnings,
                "total_suppressed": self._total_suppressed,
                "unique_keys": len(self._by_key),
                "categories": dict(self._by_category),
                "top_categories": self.top_categories(5),
                "top_keys": self.top_keys(5),
            }

    def reset(self) -> None:
        """Clear all tracked warning history."""
        with self._lock:
            self._by_key.clear()
            self._by_category.clear()
            self._total_warnings = 0
            self._total_suppressed = 0


# ---------------------------------------------------------------------------
# Warning formatter
# ---------------------------------------------------------------------------


class WarningFormatter:
    """Render ``WarningRecord`` instances as readable or structured strings."""

    def __init__(self, *, use_color: bool = True) -> None:
        self._use_color = use_color

    def format_human(self, record: WarningRecord) -> str:
        """Render a warning as a human-readable multi-line string."""
        parts: list[str] = []

        # Severity prefix
        label = self._color("WARNING", "\033[33m") if self._use_color else "WARNING"
        parts.append(f"[{label}] {record.message}")

        if record.count > 1:
            parts.append(f"    (repeated {record.count} times)")

        # Source location
        location = f"{record.filename}:{record.lineno}"
        if self._use_color:
            location = f"\033[90m{location}\033[0m"
        parts.append(f"    at {location}")
        if record.line:
            parts.append(f"      {record.line.strip()}")

        parts.append(f"    category: {record.category}")
        return "\n".join(parts)

    def format_compact(self, record: WarningRecord) -> str:
        """Render a warning as a single line."""
        return (
            f"WARNING [{record.category}] {record.message} "
            f"({record.filename}:{record.lineno})"
        )

    def format_structured(self, record: WarningRecord) -> dict[str, Any]:
        """Render a warning as a structured dict suitable for JSON logging."""
        return {
            "type": "warning",
            "message": record.message,
            "category": record.category,
            "location": {
                "filename": record.filename,
                "line": record.lineno,
            },
            "count": record.count,
            "key": record.key,
        }

    @staticmethod
    def _color(text: str, ansi_code: str) -> str:
        return f"{ansi_code}{text}\033[0m"


# ---------------------------------------------------------------------------
# Warning suppression state
# ---------------------------------------------------------------------------


class WarningSuppressionManager:
    """Thread-safe registry of suppressed warning keys, patterns, and modules.

    Bounded to ``MAX_WARNING_KEYS`` entries via a circular eviction strategy:
    when the key ring is full the oldest entry is dropped.
    """

    def __init__(self, max_keys: int = MAX_WARNING_KEYS) -> None:
        self._lock = threading.RLock()
        self._suppressed_keys: dict[str, float] = {}  # key -> first-seen timestamp
        self._max_keys = max_keys
        self._suppressed_patterns: list[re.Pattern] = []
        self._suppressed_modules: list[str] = []  # glob-like patterns
        self._suppressed_categories: set[str] = set()
        self._full_suppression: bool = False
        self._warnings_as_errors: bool = False
        self._listener: Callable[[WarningRecord], None] | None = None
        # Time-windowed deduplication: key -> last-shown timestamp
        self._cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
        self._last_shown: dict[str, float] = {}

    # ---- key-based suppression -----------------------------------------------

    def is_key_suppressed(self, key: str) -> bool:
        """Check whether *key* is in the suppression registry."""
        with self._lock:
            return key in self._suppressed_keys

    def suppress_key(self, key: str) -> bool:
        """Add *key* to the suppression set. Returns True if newly added."""
        with self._lock:
            if key in self._suppressed_keys:
                return False
            if len(self._suppressed_keys) >= self._max_keys:
                # Evict the oldest key (FIFO)
                oldest = next(iter(self._suppressed_keys))
                del self._suppressed_keys[oldest]
            import time

            self._suppressed_keys[key] = time.time()
            return True

    def unsuppress_key(self, key: str) -> bool:
        """Remove *key* from the suppression set. Returns True if it was present."""
        with self._lock:
            if key in self._suppressed_keys:
                del self._suppressed_keys[key]
                return True
            return False

    def suppress_keys(self, keys: Iterable[str]) -> int:
        """Bulk-add *keys*. Returns count of newly added keys."""
        added = 0
        for k in keys:
            if self.suppress_key(k):
                added += 1
        return added

    def clear_keys(self) -> int:
        """Remove all key-based suppressions. Returns number cleared."""
        with self._lock:
            count = len(self._suppressed_keys)
            self._suppressed_keys.clear()
            return count

    # ---- pattern-based suppression -------------------------------------------

    def suppress_pattern(self, pattern: str | re.Pattern) -> None:
        """Suppress warnings whose message matches *pattern* (regex)."""
        with self._lock:
            if isinstance(pattern, str):
                pattern = re.compile(pattern)
            if pattern not in self._suppressed_patterns:
                self._suppressed_patterns.append(pattern)

    def remove_pattern(self, pattern: str | re.Pattern) -> bool:
        """Remove a previously added message pattern. Returns True if found."""
        with self._lock:
            if isinstance(pattern, str):
                pattern = re.compile(pattern)
            for i, p in enumerate(self._suppressed_patterns):
                if p.pattern == pattern.pattern and p.flags == pattern.flags:
                    self._suppressed_patterns.pop(i)
                    return True
            return False

    def clear_patterns(self) -> int:
        """Remove all pattern-based suppressions."""
        with self._lock:
            count = len(self._suppressed_patterns)
            self._suppressed_patterns.clear()
            return count

    # ---- module-based suppression --------------------------------------------

    def suppress_module(self, module_glob: str) -> None:
        """Suppress all warnings from files matching *module_glob*.

        Example: ``suppress_module("hare/utils/deprecated/*")``
        """
        with self._lock:
            if module_glob not in self._suppressed_modules:
                self._suppressed_modules.append(module_glob)

    def remove_module(self, module_glob: str) -> bool:
        """Remove a previously added module glob. Returns True if found."""
        with self._lock:
            try:
                self._suppressed_modules.remove(module_glob)
                return True
            except ValueError:
                return False

    def clear_modules(self) -> int:
        """Remove all module-based suppressions."""
        with self._lock:
            count = len(self._suppressed_modules)
            self._suppressed_modules.clear()
            return count

    # ---- category-based suppression ------------------------------------------

    def suppress_category(self, category: str) -> None:
        """Suppress all warnings of a given category (e.g. ``DeprecationWarning``)."""
        with self._lock:
            self._suppressed_categories.add(category)

    def remove_category(self, category: str) -> bool:
        """Remove a previously suppressed category. Returns True if found."""
        with self._lock:
            try:
                self._suppressed_categories.remove(category)
                return True
            except KeyError:
                return False

    def clear_categories(self) -> int:
        """Remove all category-based suppressions."""
        with self._lock:
            count = len(self._suppressed_categories)
            self._suppressed_categories.clear()
            return count

    # ---- global suppression --------------------------------------------------

    @property
    def full_suppression(self) -> bool:
        """Whether all warnings are globally suppressed."""
        with self._lock:
            return self._full_suppression

    @full_suppression.setter
    def full_suppression(self, value: bool) -> None:
        with self._lock:
            self._full_suppression = value

    # ---- warnings-as-errors --------------------------------------------------

    @property
    def warnings_as_errors(self) -> bool:
        """Whether warnings are raised as exceptions."""
        with self._lock:
            return self._warnings_as_errors

    @warnings_as_errors.setter
    def warnings_as_errors(self, value: bool) -> None:
        with self._lock:
            self._warnings_as_errors = value

    # ---- cooldown / time-windowed deduplication ------------------------------

    @property
    def cooldown_seconds(self) -> float:
        """Seconds before an identical warning can be shown again."""
        with self._lock:
            return self._cooldown_seconds

    @cooldown_seconds.setter
    def cooldown_seconds(self, value: float) -> None:
        with self._lock:
            self._cooldown_seconds = max(0.0, value)

    def is_in_cooldown(self, key: str) -> bool:
        """Return True if *key* was shown within the cooldown window."""
        with self._lock:
            if self._cooldown_seconds <= 0:
                return False
            last = self._last_shown.get(key)
            if last is None:
                return False
            import time

            return (time.time() - last) < self._cooldown_seconds

    def mark_shown(self, key: str) -> None:
        """Record that *key* was just displayed, starting its cooldown."""
        import time

        with self._lock:
            self._last_shown[key] = time.time()
            # Prune stale entries when the dict grows large
            if len(self._last_shown) > self._max_keys:
                now = time.time()
                stale = [
                    k
                    for k, ts in self._last_shown.items()
                    if (now - ts) > self._cooldown_seconds
                ]
                for k in stale:
                    del self._last_shown[k]

    # ---- listener ------------------------------------------------------------

    def set_listener(self, listener: Callable[[WarningRecord], None] | None) -> None:
        """Register (or clear) a callback invoked for every displayed warning."""
        with self._lock:
            self._listener = listener

    # ---- predicate: should this warning be shown? ----------------------------

    def should_show(self, record: WarningRecord) -> bool:
        """Return False if the warning is suppressed by any active rule."""
        with self._lock:
            # Full global suppression takes priority.
            if self._full_suppression:
                return False

            # Key-based check
            if record.key in self._suppressed_keys:
                return False

            # Category-based check
            if record.category in self._suppressed_categories:
                return False

            # Message pattern check
            for pattern in self._suppressed_patterns:
                if record.matches_pattern(pattern):
                    return False

            # Module glob check
            for module_glob in self._suppressed_modules:
                if record.matches_module(module_glob):
                    return False

            return True

    def notify_listener(self, record: WarningRecord) -> None:
        """Invoke the registered listener (if any) for *record*."""
        with self._lock:
            listener = self._listener
        if listener is not None:
            try:
                listener(record)
            except Exception:
                pass

    # ---- inspection ----------------------------------------------------------

    def suppression_count(self) -> int:
        """Return total number of active suppression rules."""
        with self._lock:
            return (
                len(self._suppressed_keys)
                + len(self._suppressed_patterns)
                + len(self._suppressed_modules)
                + len(self._suppressed_categories)
                + (1 if self._full_suppression else 0)
            )

    def get_suppression_report(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of all suppression rules."""
        with self._lock:
            return {
                "suppressed_keys": list(self._suppressed_keys.keys()),
                "suppressed_patterns": [p.pattern for p in self._suppressed_patterns],
                "suppressed_modules": list(self._suppressed_modules),
                "suppressed_categories": sorted(self._suppressed_categories),
                "full_suppression": self._full_suppression,
                "warnings_as_errors": self._warnings_as_errors,
                "total_rules": self.suppression_count(),
            }


# ---------------------------------------------------------------------------
# Warning display engine
# ---------------------------------------------------------------------------


@dataclass
class WarningDisplayConfig:
    """Configuration for warning rendering."""

    severity: WarningSeverity = WarningSeverity.ALWAYS
    use_color: bool = True
    compact: bool = False
    structured: bool = False
    show_stack: bool = False
    max_repeat_count: int = 100


class WarningDisplay:
    """Formats and routes warnings to the appropriate output channel.

    Integrates with the telemetry ``LogManager`` when available, falling back
    to stderr and ``warnings.warn`` for plain Python warnings.
    """

    def __init__(
        self,
        config: WarningDisplayConfig | None = None,
        formatter: WarningFormatter | None = None,
    ) -> None:
        self.config = config or WarningDisplayConfig()
        self.formatter = formatter or WarningFormatter(use_color=self.config.use_color)
        self._log_manager: Any = None  # lazily resolved
        self._history = WarningHistory()
        # Track per-key display counts for rate limiting
        self._key_counts: dict[str, int] = {}

    # ---- log-manager integration ---------------------------------------------

    def _resolve_log_manager(self) -> Any:
        """Attempt to resolve the telemetry LogManager singleton.

        Returns None when telemetry is not initialized (e.g. early boot).
        """
        if self._log_manager is None:
            try:
                from hare.utils.telemetry.logger import LogManager

                self._log_manager = LogManager.get_instance()
            except (ImportError, AttributeError):
                pass
        return self._log_manager

    # ---- display -------------------------------------------------------------

    def display(
        self, record: WarningRecord, *, suppression: Any = None
    ) -> None:
        """Render *record* according to the active config and route to outputs.

        Applies rate limiting via ``max_repeat_count`` and time-window
        deduplication via the suppression manager's cooldown.
        """
        # Resolve severity override from environment
        severity = WarningSeverity.from_string(
            os.environ.get(_WARNING_LEVEL_ENV, "default")
        )
        if severity == WarningSeverity.IGNORE:
            self._history.record_suppressed(record)
            return

        quiet = os.environ.get(_QUIET_ENV) == "1"
        if quiet and severity != WarningSeverity.ERROR:
            self._history.record_suppressed(record)
            return

        # Time-window deduplication via suppression manager
        if suppression is not None and hasattr(suppression, "is_in_cooldown"):
            if suppression.is_in_cooldown(record.key):
                self._history.record_suppressed(record)
                return

        # Rate limiting: cap repeat count
        count = self._key_counts.get(record.key, 0) + 1
        self._key_counts[record.key] = count
        max_repeat = self.config.max_repeat_count
        if max_repeat > 0 and count > max_repeat:
            if count == max_repeat + 1:
                # Emit a summary line once instead of silencing silently
                self._emit_line(
                    f"[WARNING] (further repetitions of \"{record.message[:80]}\" "
                    f"suppressed — shown {max_repeat} times)"
                )
            self._history.record_suppressed(record)
            return

        record.count = count

        # Mark cooldown timestamp
        if suppression is not None and hasattr(suppression, "mark_shown"):
            suppression.mark_shown(record.key)

        # Format the warning
        if self.config.structured:
            structured = self.formatter.format_structured(record)
            self._emit_structured(structured)
        elif self.config.compact:
            line = self.formatter.format_compact(record)
            self._emit_line(line)
        else:
            text = self.formatter.format_human(record)
            if self.config.show_stack:
                text += "\n" + traceback.format_stack()[-5]
            self._emit_line(text)

        self._history.record_seen(record)

    def _emit_line(self, text: str) -> None:
        """Write a textual warning to the appropriate outputs."""
        # Try the telemetry logger first
        mgr = self._resolve_log_manager()
        if mgr is not None:
            mgr.warn("warnings", text)
        else:
            # Fallback: stderr
            import sys

            print(text, file=sys.stderr)

    def _emit_structured(self, data: dict[str, Any]) -> None:
        """Emit a structured warning payload."""
        mgr = self._resolve_log_manager()
        if mgr is not None:
            mgr.warn("warnings", data.get("message", ""), metadata=data)
        else:
            import json
            import sys

            print(json.dumps(data, default=str), file=sys.stderr)

    # ---- warning-formatting utility ------------------------------------------

    def make_record(
        self,
        message: str,
        *,
        category: str = "UserWarning",
        filename: str = "<string>",
        lineno: int = 0,
        line: str | None = None,
        count: int = 1,
    ) -> WarningRecord:
        """Create a ``WarningRecord`` from programmatic input."""
        import time

        return WarningRecord(
            message=message,
            category=category,
            filename=filename,
            lineno=lineno,
            line=line,
            count=count,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Custom warnings filter for Python's ``warnings`` module
# ---------------------------------------------------------------------------


class HareWarningsFilter:
    """A ``warnings``-module compatible filter that delegates to the suppression
    manager and routes surviving warnings through the display engine."""

    def __init__(
        self,
        suppression: WarningSuppressionManager,
        display: WarningDisplay,
    ) -> None:
        self._suppression = suppression
        self._display = display
        # Track warnings that have already been shown (for "once" semantics)
        self._seen_keys: set[str] = set()

    def __call__(
        self,
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> bool | None:
        """Return True to suppress, None to let the default action proceed."""
        msg_text = str(message)

        record = WarningRecord(
            message=msg_text,
            category=category.__name__,
            filename=filename,
            lineno=lineno,
            line=line,
        )

        # Check if suppressed
        if not self._suppression.should_show(record):
            return True  # suppress

        # Warnings-as-errors: raise
        if self._suppression.warnings_as_errors:
            raise category(msg_text)

        # Enforce "once" semantics for warning handler itself
        if record.key in self._seen_keys:
            return True  # suppress duplicate in this process
        self._seen_keys.add(record.key)

        # Display the warning
        self._display.display(record, suppression=self._suppression)
        self._suppression.notify_listener(record)

        return True  # suppress default action (we handled it already)


# ---------------------------------------------------------------------------
# Global singleton state
# ---------------------------------------------------------------------------

_suppression_manager: WarningSuppressionManager | None = None
_display: WarningDisplay | None = None
_filter: HareWarningsFilter | None = None
_initialized: bool = False
_init_lock: threading.Lock = threading.Lock()


def _get_suppression() -> WarningSuppressionManager:
    """Return the global suppression manager (lazy init)."""
    global _suppression_manager
    if _suppression_manager is None:
        with _init_lock:
            if _suppression_manager is None:
                _suppression_manager = WarningSuppressionManager()
    return _suppression_manager


def _get_display() -> WarningDisplay:
    """Return the global warning display engine (lazy init)."""
    global _display
    if _display is None:
        with _init_lock:
            if _display is None:
                structured = os.environ.get("CLAUDE_CODE_LOG_JSON") == "1"
                use_color = not os.environ.get("NO_COLOR")
                _display = WarningDisplay(
                    config=WarningDisplayConfig(
                        structured=structured,
                        use_color=use_color,
                        compact=not os.isatty(2),
                    )
                )
    return _display


def _get_filter() -> HareWarningsFilter:
    """Return the global warnings filter (lazy init)."""
    global _filter
    if _filter is None:
        with _init_lock:
            if _filter is None:
                _filter = HareWarningsFilter(
                    suppression=_get_suppression(),
                    display=_get_display(),
                )
    return _filter


# ---------------------------------------------------------------------------
# Public API — Initialization
# ---------------------------------------------------------------------------


def initialize_warning_handler() -> None:
    """Route Python warnings through Hare's warning management system.

    Installs ``HareWarningsFilter`` as the sole ``warnings`` filter, routes
    all Python warnings through the suppression manager and display engine,
    and enables ``logging.captureWarnings`` as a secondary path for
    warnings that bypass ``warnings.warn`` directly.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return

        # Un-suppress default Python filters so our filter gets everything
        warnings.resetwarnings()

        # Install our custom filter
        filt = _get_filter()
        warnings.simplefilter("always")
        # Install as the sole filter: our filter suppresses what we want,
        # and handles the rest by displaying and suppressing default action.
        warnings.filterwarnings("always")
        warnings.simplefilter("always")

        # Register the filter as a custom handler via showwarning
        def _showwarning(
            message: Warning | str,
            category: type[Warning],
            filename: str,
            lineno: int,
            file: Any = None,
            line: str | None = None,
        ) -> None:
            filt(message, category, filename, lineno, file, line)

        warnings.showwarning = _showwarning  # type: ignore[assignment]

        # Also route via logging as a fallback
        logging.captureWarnings(True)

        _initialized = True


def reset_warning_handler() -> None:
    """Remove the custom warning handler and restore Python defaults.

    Clears all suppression state, resets ``warnings.showwarning``, and
    disables ``logging.captureWarnings``.
    """
    global _initialized, _suppression_manager, _display, _filter
    with _init_lock:
        warnings.resetwarnings()
        logging.captureWarnings(False)

        if _display is not None:
            _display.config = WarningDisplayConfig()
        if _suppression_manager is not None:
            _suppression_manager.clear_categories()
            _suppression_manager.clear_keys()
            _suppression_manager.clear_modules()
            _suppression_manager.clear_patterns()
            _suppression_manager.full_suppression = False
            _suppression_manager.warnings_as_errors = False
            _suppression_manager.set_listener(None)

        _suppression_manager = None
        _display = None
        _filter = None
        _initialized = False


# ---------------------------------------------------------------------------
# Public API — Suppression
# ---------------------------------------------------------------------------


def suppress_warning_by_key(key: str) -> bool:
    """Suppress future warnings matching *key*."""
    return _get_suppression().suppress_key(key)


def unsuppress_warning_by_key(key: str) -> bool:
    """Allow warnings matching *key* to be shown again."""
    return _get_suppression().unsuppress_key(key)


def suppress_warnings_from_module(module_glob: str) -> None:
    """Suppress all warnings originating from files matching *module_glob*.

    Example::

        suppress_warnings_from_module("hare/utils/third_party/*")
    """
    _get_suppression().suppress_module(module_glob)


def unsuppress_warnings_from_module(module_glob: str) -> bool:
    """Remove a module-glob suppression rule."""
    return _get_suppression().remove_module(module_glob)


def suppress_warnings_by_pattern(pattern: str | re.Pattern) -> None:
    """Suppress warnings whose message matches *pattern* (regex or substring)."""
    _get_suppression().suppress_pattern(pattern)


def unsuppress_warnings_by_pattern(pattern: str | re.Pattern) -> bool:
    """Remove a message-pattern suppression rule."""
    return _get_suppression().remove_pattern(pattern)


def suppress_warnings_by_category(category: str) -> None:
    """Suppress all warnings of *category* (e.g. ``DeprecationWarning``)."""
    _get_suppression().suppress_category(category)


def unsuppress_warnings_by_category(category: str) -> bool:
    """Remove a category suppression rule."""
    return _get_suppression().remove_category(category)


def suppress_all_warnings() -> None:
    """Globally suppress all warnings."""
    _get_suppression().full_suppression = True


def unsuppress_all_warnings() -> None:
    """Remove global suppression and all individual rules."""
    mgr = _get_suppression()
    mgr.full_suppression = False
    mgr.clear_keys()
    mgr.clear_patterns()
    mgr.clear_modules()
    mgr.clear_categories()


def set_warnings_as_errors(enabled: bool = True) -> None:
    """Enable or disable warnings-as-errors mode."""
    _get_suppression().warnings_as_errors = enabled


def is_warning_suppressed(key: str) -> bool:
    """Check whether a specific *key* is suppressed."""
    return _get_suppression().is_key_suppressed(key)


def get_suppression_report() -> dict[str, Any]:
    """Return a JSON-serializable summary of all active suppression rules."""
    return _get_suppression().get_suppression_report()


# ---------------------------------------------------------------------------
# Public API — Display
# ---------------------------------------------------------------------------


def display_warning(
    message: str,
    *,
    category: str = "UserWarning",
    filename: str = "<string>",
    lineno: int = 0,
    line: str | None = None,
    suppress_key: str | None = None,
) -> WarningRecord:
    """Format and display a programmatic warning.

    If *suppress_key* is provided, the warning will be suppressed in the
    future (equivalent to calling ``suppress_warning_by_key`` first).

    Returns the ``WarningRecord`` that was displayed.
    """
    record = _get_display().make_record(
        message=message,
        category=category,
        filename=filename,
        lineno=lineno,
        line=line,
    )

    suppression = _get_suppression()

    if suppress_key is not None:
        suppression.suppress_key(suppress_key)
        # Also suppress by the record's natural key
        suppression.suppress_key(record.key)

    if not suppression.should_show(record):
        return record

    _get_display().display(record, suppression=suppression)
    suppression.notify_listener(record)
    return record


def set_warning_listener(listener: Callable[[WarningRecord], None] | None) -> None:
    """Register a callback invoked for every displayed warning."""
    _get_suppression().set_listener(listener)


def get_warning_history_summary() -> dict[str, Any]:
    """Return a JSON-serializable summary of all warnings seen."""
    return _get_display()._history.get_summary()


def get_warning_top_categories(n: int = 10) -> list[tuple[str, int]]:
    """Return the top *n* warning categories by count."""
    return _get_display()._history.top_categories(n)


def reset_warning_history() -> None:
    """Clear all tracked warning statistics."""
    _get_display()._history.reset()
    _get_display()._key_counts.clear()


def set_warning_cooldown(seconds: float) -> None:
    """Set the minimum interval (in seconds) before an identical warning can
    be shown again.  Set to 0 to disable cooldown.
    """
    _get_suppression().cooldown_seconds = seconds


def set_warning_display_config(
    *,
    severity: str | None = None,
    use_color: bool | None = None,
    compact: bool | None = None,
    structured: bool | None = None,
    show_stack: bool | None = None,
) -> None:
    """Update the global warning display configuration."""
    config = _get_display().config
    if severity is not None:
        config.severity = WarningSeverity.from_string(severity)
    if use_color is not None:
        config.use_color = use_color
    if compact is not None:
        config.compact = compact
    if structured is not None:
        config.structured = structured
    if show_stack is not None:
        config.show_stack = show_stack
    _get_display().formatter._use_color = config.use_color


# ---------------------------------------------------------------------------
# Public API — Decorators and single-shot utilities
# ---------------------------------------------------------------------------


def deprecated(
    since: str = "",
    replacement: str = "",
    *,
    category: str = "DeprecationWarning",
    stacklevel: int = 2,
) -> Callable[[Callable], Callable]:
    """Decorator that emits a ``DeprecationWarning`` when a function is called.

    Usage::

        @deprecated(since="2.0", replacement="new_function()")
        def old_function():
            ...

    Args:
        since: Version string indicating when deprecation started.
        replacement: Suggested replacement API.
        category: Warning category name (default ``DeprecationWarning``).
        stacklevel: How far up the call stack the warning points.
    """

    def decorator(func: Callable) -> Callable:
        func_name = getattr(func, "__qualname__", func.__name__)
        msg_parts = [f"'{func_name}' is deprecated"]
        if since:
            msg_parts.append(f"since {since}")
        if replacement:
            msg_parts.append(f"— use '{replacement}' instead")
        msg = ". ".join(msg_parts) + "."

        import functools

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import warnings as _w

            _w.warn(msg, DeprecationWarning, stacklevel=stacklevel)
            return func(*args, **kwargs)

        return wrapper

    return decorator


_warn_once_seen: set[str] = set()
_warn_once_lock = threading.Lock()


def warn_once(
    message: str,
    *,
    category: str = "UserWarning",
    key: str | None = None,
) -> None:
    """Display *message* as a warning exactly once per process lifetime.

    Subsequent calls with the same *message* (or explicit *key*) are
    silently ignored.

    Usage::

        warn_once("Experimental feature X is enabled", category="FutureWarning")
    """
    dedup_key = key or re.sub(r"\s+", " ", message).strip()
    with _warn_once_lock:
        if dedup_key in _warn_once_seen:
            return
        _warn_once_seen.add(dedup_key)

    display_warning(message, category=category)


def install_default_warnings_filter() -> None:
    """Install Python's built-in ``default`` warnings filter and then overlay
    the Hare warnings filter on top.

    This is a convenience for application-entry scripts that want both
    the interpreter's default filters (e.g. ``__pycache__``, ``-W`` flags)
    and Hare's suppression / display machinery.

    Usage::

        from hare.utils.warning_handler import install_default_warnings_filter
        install_default_warnings_filter()
    """
    import warnings as _w

    _w.resetwarnings()
    # Restore Python's default simplefilters
    _w.simplefilter("default")
    # Then install Hare's handler
    initialize_warning_handler()


# ---------------------------------------------------------------------------
# Public API — Batch operations (context managers)
# ---------------------------------------------------------------------------


class SuppressedWarnings:
    """Context manager that suppresses all warnings within a block.

    Usage::

        with SuppressedWarnings():
            import deprecated_module  # warnings are hidden

        with SuppressedWarnings(category="DeprecationWarning"):
            call_deprecated_api()
    """

    def __init__(
        self,
        *,
        key: str | None = None,
        pattern: str | None = None,
        module: str | None = None,
        category: str | None = None,
        full: bool = False,
    ) -> None:
        """Configure suppression rules for the context block.

        Args:
            key: A specific warning key to suppress.
            pattern: Message regex to suppress.
            module: Module glob to suppress.
            category: Warning category to suppress.
            full: If True, suppress all warnings in the block.
        """
        self._key = key
        self._pattern = pattern
        self._module = module
        self._category = category
        self._full = full
        self._suppressed = False

    def __enter__(self) -> SuppressedWarnings:
        mgr = _get_suppression()
        if self._full:
            mgr.full_suppression = True
            self._suppressed = True
            return self
        if self._key:
            mgr.suppress_key(self._key)
            self._key = self._key  # keep for cleanup
            self._suppressed = True
        if self._pattern:
            mgr.suppress_pattern(self._pattern)
            self._suppressed = True
        if self._module:
            mgr.suppress_module(self._module)
            self._suppressed = True
        if self._category:
            mgr.suppress_category(self._category)
            self._suppressed = True
        return self

    def __exit__(self, *args: Any) -> None:
        if not self._suppressed:
            return
        mgr = _get_suppression()
        if self._full:
            mgr.full_suppression = False
        if self._key:
            mgr.unsuppress_key(self._key)
        if self._pattern:
            mgr.remove_pattern(self._pattern)
        if self._module:
            mgr.remove_module(self._module)
        if self._category:
            mgr.remove_category(self._category)


# ============================================================================
# Warning trend tracking — time-windowed frequency analysis
# ============================================================================


@dataclass
class TrendBucket:
    """A count of warnings within a specific time window."""

    start_time: float
    count: int = 0
    keys: set[str] = field(default_factory=set)

    @property
    def unique_keys(self) -> int:
        return len(self.keys)

    @property
    def age_seconds(self) -> float:
        import time

        return time.time() - self.start_time


class WarningTrendTracker:
    """Tracks warning frequencies across sliding time windows.

    Provides spike detection, trend direction (increasing / decreasing /
    stable), and per-category velocity.  Drives the escalation policy
    below.

    Thread-safe.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        num_buckets: int = 6,
        spike_multiplier: float = 3.0,
        history: WarningHistory | None = None,
    ) -> None:
        self._window_seconds = window_seconds
        self._num_buckets = num_buckets
        self._spike_multiplier = spike_multiplier
        self._history = history
        self._buckets: list[TrendBucket] = []
        self._lock = threading.RLock()
        self._base_rate: float = 0.0  # exponentially-weighted moving average

    # ---- recording -----------------------------------------------------------

    def record(self, record: WarningRecord) -> None:
        """Log that *record* was displayed."""
        import time

        now = time.time()
        with self._lock:
            self._rotate(now)
            if not self._buckets:
                self._buckets.append(TrendBucket(start_time=now))
            current = self._buckets[-1]
            current.count += 1
            current.keys.add(record.key)
            # Update EWMA
            alpha = 0.2
            self._base_rate = self._base_rate * (1 - alpha) + alpha * 1.0

    def _rotate(self, now: float) -> None:
        """Drop expired buckets and ensure the active bucket is current."""
        cutoff = now - (self._window_seconds * self._num_buckets)
        self._buckets = [b for b in self._buckets if b.start_time >= cutoff]
        if not self._buckets:
            return
        # If the newest bucket is older than one window, start a fresh one
        if now - self._buckets[-1].start_time >= self._window_seconds:
            self._buckets.append(TrendBucket(start_time=now))
        # Cap total buckets
        while len(self._buckets) > self._num_buckets:
            self._buckets.pop(0)

    # ---- queries -------------------------------------------------------------

    def recent_count(self, seconds: float | None = None) -> int:
        """Total warnings in the last *seconds* (or one full window)."""
        import time

        now = time.time()
        window = seconds if seconds is not None else self._window_seconds
        cutoff = now - window
        with self._lock:
            return sum(b.count for b in self._buckets if b.start_time >= cutoff)

    def recent_unique(self, seconds: float | None = None) -> int:
        """Unique warning keys in the last *seconds*."""
        import time

        now = time.time()
        window = seconds if seconds is not None else self._window_seconds
        cutoff = now - window
        seen: set[str] = set()
        with self._lock:
            for b in self._buckets:
                if b.start_time >= cutoff:
                    seen.update(b.keys)
        return len(seen)

    def is_spiking(self) -> bool:
        """Return True if the current rate exceeds the baseline by *spike_multiplier*."""
        with self._lock:
            if self._base_rate <= 0:
                return False
            current_rate = self.recent_count(self._window_seconds) / self._window_seconds
            return current_rate > self._base_rate * self._spike_multiplier

    def trend(self) -> str:
        """Return ``"increasing"``, ``"decreasing"``, or ``"stable"``."""
        import time

        with self._lock:
            if len(self._buckets) < 2:
                return "stable"
            now = time.time()
            half = len(self._buckets) // 2
            recent = sum(
                b.count for b in self._buckets[-half:] if now - b.start_time <= self._window_seconds * half
            )
            older = sum(
                b.count for b in self._buckets[:half]
            )
            if recent > older * 1.2:
                return "increasing"
            if recent < older * 0.8:
                return "decreasing"
            return "stable"

    def get_status(self) -> dict[str, Any]:
        """Return a JSON-serializable trend status snapshot."""
        import time

        with self._lock:
            return {
                "recent_count": self.recent_count(),
                "recent_unique": self.recent_unique(),
                "is_spiking": self.is_spiking(),
                "trend": self.trend(),
                "base_rate": round(self._base_rate, 4),
                "buckets": len(self._buckets),
                "window_seconds": self._window_seconds,
                "total_tracked": sum(b.count for b in self._buckets),
            }

    def reset(self) -> None:
        """Clear all trend data."""
        with self._lock:
            self._buckets.clear()
            self._base_rate = 0.0


# ============================================================================
# Warning escalation — auto-promote repeated warnings to errors
# ============================================================================


class WarningEscalationPolicy:
    """Configurable policy that escalates warnings to errors when
    frequency exceeds a threshold.

    Escalation can:
    - Raise an exception (``warnings_as_errors`` mode)
    - Emit to the error log
    - Invoke a user-supplied callback

    Thread-safe.
    """

    def __init__(
        self,
        *,
        max_per_window: int = 50,
        window_seconds: float = 60.0,
        raise_on_escalate: bool = False,
    ) -> None:
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self.raise_on_escalate = raise_on_escalate
        self._trend = WarningTrendTracker(
            window_seconds=window_seconds,
            spike_multiplier=2.0,
        )
        self._escalated_keys: set[str] = set()
        self._on_escalate: Callable[[WarningRecord, str], None] | None = None
        self._lock = threading.RLock()
        self._escalation_count: int = 0

    def record_and_check(self, record: WarningRecord) -> bool:
        """Record *record* and return True if it should be escalated.

        When True is returned the caller should treat the warning as an
        error (e.g. raise, abort, or emit to error log).
        """
        self._trend.record(record)
        with self._lock:
            recent = self._trend.recent_count(self.window_seconds)
            if recent >= self.max_per_window:
                if record.key not in self._escalated_keys:
                    self._escalated_keys.add(record.key)
                    self._escalation_count += 1
                    self._invoke_callback(record, f"threshold: {recent} >= {self.max_per_window} in {self.window_seconds}s")
                return True
            if self._trend.is_spiking():
                if record.key not in self._escalated_keys:
                    self._escalated_keys.add(record.key)
                    self._escalation_count += 1
                    self._invoke_callback(record, "spike detected")
                return True
        return False

    def on_escalate(self, callback: Callable[[WarningRecord, str], None]) -> None:
        """Register a callback invoked when escalation fires.

        The callback receives the triggering ``WarningRecord`` and a
        reason string.
        """
        with self._lock:
            self._on_escalate = callback

    def _invoke_callback(self, record: WarningRecord, reason: str) -> None:
        cb = self._on_escalate
        if cb is not None:
            try:
                cb(record, reason)
            except Exception:
                pass

    @property
    def escalation_count(self) -> int:
        with self._lock:
            return self._escalation_count

    def get_status(self) -> dict[str, Any]:
        """Return a JSON-serializable escalation summary."""
        with self._lock:
            return {
                "escalation_count": self._escalation_count,
                "escalated_keys": sorted(self._escalated_keys),
                "max_per_window": self.max_per_window,
                "window_seconds": self.window_seconds,
                "trend": self._trend.get_status(),
            }

    def reset(self) -> None:
        """Clear all escalation state."""
        with self._lock:
            self._escalated_keys.clear()
            self._escalation_count = 0
            self._trend.reset()


# ============================================================================
# Warning correlation — group related warnings under a context ID
# ============================================================================

_correlation_stack: list[str] = []
_correlation_lock = threading.Lock()


class WarningCorrelation:
    """Context manager that tags all warnings emitted within the block
    with a correlation ID so they can be grouped in logs.

    Supports nested correlations — the innermost active ID is used.

    Usage::

        with WarningCorrelation("agent-init"):
            load_config()          # warnings tagged "agent-init"
            with WarningCorrelation("plugin-foo"):
                init_plugin()      # warnings tagged "plugin-foo"
    """

    def __init__(self, correlation_id: str) -> None:
        self._id = correlation_id

    def __enter__(self) -> "WarningCorrelation":
        with _correlation_lock:
            _correlation_stack.append(self._id)
        return self

    def __exit__(self, *args: Any) -> None:
        with _correlation_lock:
            if _correlation_stack and _correlation_stack[-1] == self._id:
                _correlation_stack.pop()
            else:
                # Mismatched pop — remove the id wherever it is
                try:
                    _correlation_stack.remove(self._id)
                except ValueError:
                    pass

    @staticmethod
    def current() -> str | None:
        """Return the innermost active correlation ID, or None."""
        with _correlation_lock:
            return _correlation_stack[-1] if _correlation_stack else None

    @staticmethod
    def active_ids() -> list[str]:
        """Return all active correlation IDs (outermost first)."""
        with _correlation_lock:
            return list(_correlation_stack)


def get_current_correlation_id() -> str | None:
    """Convenience accessor for the active correlation ID."""
    return WarningCorrelation.current()


# ============================================================================
# Warning remediation — actionable suggestions for common patterns
# ============================================================================

# Map of (regex pattern, suggestion) for common warning messages.
_REMEDIATION_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"is deprecated(?:.*use\s+(['\"]?)(?P<replacement>[^'\".]+)\\1)?", re.I),
        "Replace deprecated API usage. See the deprecation notice for the recommended alternative.",
    ),
    (
        re.compile(r"import.*could not be resolved", re.I),
        "Install the missing package or update PYTHONPATH to include its location.",
    ),
    (
        re.compile(r"resource.*not closed|unclosed|leaked", re.I),
        "Ensure the resource is closed with a context manager (`with` statement) or explicit `.close()`.",
    ),
    (
        re.compile(r"overflow|overflow encountered", re.I),
        "Check for division by very small numbers or use a wider numeric type (e.g. float64 -> float128).",
    ),
    (
        re.compile(r"invalid escape sequence", re.I),
        "Use a raw string (r'...') or escape backslashes properly in the string literal.",
    ),
    (
        re.compile(r"coroutine.*was never awaited", re.I),
        "Add `await` to the coroutine call, or schedule it with `asyncio.create_task()`.",
    ),
    (
        re.compile(r"runtimewarning.*coroutine", re.I),
        "Add `await` to the coroutine call, or schedule it with `asyncio.create_task()`.",
    ),
    (
        re.compile(r"field.*shadow|overwrite.*field|duplicate.*field", re.I),
        "Rename the duplicate field in the subclass to avoid shadowing the parent definition.",
    ),
    (
        re.compile(r"ssl|tls.*verify|certificate.*verify.*fail", re.I),
        "Check that your SSL certificates are valid and up-to-date. For testing, set `verify=False` (insecure).",
    ),
    (
        re.compile(r"out of memory|memory.*limit|could not allocate", re.I),
        "Reduce batch size, free cached data, or increase available memory. Consider streaming large datasets.",
    ),
]


def remediate_warning(message: str) -> str | None:
    """Return an actionable suggestion for a warning message, or None.

    Matches *message* against a built-in set of common warning patterns
    and returns a human-readable remediation hint.

    Usage::

        hint = remediate_warning(str(warning))
        if hint:
            print(f"Suggestion: {hint}")
    """
    for pattern, suggestion in _REMEDIATION_RULES:
        if pattern.search(message):
            return suggestion
    return None


def add_remediation_rule(pattern: str | re.Pattern, suggestion: str) -> None:
    """Register a custom remediation rule.

    Args:
        pattern: Regex (compiled or string) to match against warning messages.
        suggestion: Human-readable remediation text.
    """
    if isinstance(pattern, str):
        pattern = re.compile(pattern)
    _REMEDIATION_RULES.insert(0, (pattern, suggestion))


def list_remediation_rules() -> list[dict[str, str]]:
    """Return all registered remediation rules."""
    return [
        {"pattern": p.pattern, "suggestion": s}
        for p, s in _REMEDIATION_RULES
    ]
