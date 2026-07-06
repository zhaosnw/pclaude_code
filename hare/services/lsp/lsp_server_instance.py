"""
Single LSP server lifecycle (stdio/jsonrpc).

Port of: src/services/lsp/LSPServerInstance.ts — protocol-shaped stub.

Manages the complete lifecycle of a single LSP server process:
  - Spawns the server process with piped stdio
  - Runs the LSP initialize handshake
  - Reads JSON-RPC messages from stdout via a background reader task
  - Correlates requests with responses via sequence numbers
  - Dispatches server-to-client notifications and requests to registered handlers
  - Handles graceful shutdown (shutdown + exit) and forced process kill
  - Detects process crashes and transitions to error state
  - Supports restart with configurable backoff
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LSP_INITIALIZE_TIMEOUT = 30.0       # seconds to wait for initialize response
LSP_SHUTDOWN_TIMEOUT = 5.0          # seconds to wait for shutdown response
LSP_REQUEST_TIMEOUT = 30.0          # default per-request timeout
LSP_RESTART_DELAY_BASE = 0.1        # base seconds for restart backoff
LSP_MAX_RESTART_DELAY = 30.0        # cap for restart backoff
LSP_MAX_RESTART_COUNT = 5           # give up after this many consecutive failures
LSP_READER_BUFFER_SIZE = 64 * 1024  # stdout read chunk size

# Well-known LSP methods used during lifecycle
_METHOD_INITIALIZE = "initialize"
_METHOD_INITIALIZED = "initialized"
_METHOD_SHUTDOWN = "shutdown"
_METHOD_EXIT = "exit"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScopedLspServerConfig:
    """Configuration for a single scoped LSP server instance."""

    name: str
    command: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    initialization_options: dict[str, Any] = field(default_factory=dict)
    root_uri: str = ""


@dataclass
class LspServerStateInfo:
    """Immutable snapshot of server state for diagnostics / status display."""

    name: str
    state: str
    start_time: Optional[datetime] = None
    restart_count: int = 0
    initialized: bool = False
    last_error_message: str = ""
    uptime_seconds: Optional[float] = None


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class LspServerError(Exception):
    """Raised when the LSP server encounters a fatal error."""


class LspInitializeError(LspServerError):
    """Raised when the LSP initialize handshake fails."""


class LspProcessExitedError(LspServerError):
    """Raised when the server process exits unexpectedly."""


class LspTimeoutError(LspServerError):
    """Raised when an LSP request times out."""


# ---------------------------------------------------------------------------
# LspServerInstance
# ---------------------------------------------------------------------------

@dataclass
class LspServerInstance:
    """Manages the lifecycle and JSON-RPC transport for a single LSP server.

    State machine::

        stopped ──start()──▶ starting ──[init ok]──▶ running
           ▲                    │                      │
           │                    ▼                      │
           │                  error ◀──[crash]─────────┤
           │                    │                      │
           └────stop()────◀ stopping ◀────stop()───────┘

    All public state mutations are guarded by an internal lock so the instance
    is safe to drive from concurrent tasks (e.g. a health-check loop).
    """

    # -- config / identity ---------------------------------------------------
    name: str
    config: ScopedLspServerConfig

    # -- observable state ----------------------------------------------------
    state: str = "stopped"
    start_time: Optional[datetime] = None
    last_error: Optional[BaseException] = None
    restart_count: int = 0

    # -- server capabilities (populated after initialize) ---------------------
    server_capabilities: dict[str, Any] = field(default_factory=dict)

    # -- private: subprocess -------------------------------------------------
    _process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    _seq: int = field(default=0, repr=False)
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict, repr=False)
    _reader_task: Optional[asyncio.Task[None]] = field(default=None, repr=False)
    _initialized: bool = field(default=False, repr=False)

    # -- private: handler registries -----------------------------------------
    _notifications: dict[str, list[Callable[[Any], None]]] = field(
        default_factory=dict, repr=False
    )
    _request_handlers: dict[str, Callable[[Any], Any]] = field(
        default_factory=dict, repr=False
    )

    # -- private: synchronisation --------------------------------------------
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _stderr_buffer: list[str] = field(default_factory=list, repr=False)

    # ========================================================================
    # Public API
    # ========================================================================

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Spawn the LSP server process, perform the initialize handshake.

        Transitions: ``stopped | error`` → ``starting`` → ``running`` (or ``error``).

        Raises ``LspInitializeError`` if the handshake fails.
        """
        async with self._lock:
            if self.state in ("running", "starting"):
                logger.debug("LSP server %r already %s; skipping start.", self.name, self.state)
                return

            self.state = "starting"
            self.last_error = None
            self._initialized = False
            self.server_capabilities = {}
            self._stderr_buffer = []

        command = self.config.command
        if not command:
            await self._transition_to_error(
                LspInitializeError(f"No command configured for LSP server {self.name!r}")
            )
            return

        try:
            # Spawn the process
            kwargs: dict[str, Any] = {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if self.config.cwd:
                kwargs["cwd"] = self.config.cwd
            env: Optional[dict[str, str]] = None
            if self.config.env:
                import os
                env = os.environ.copy()
                env.update(self.config.env)
            if env is not None:
                kwargs["env"] = env

            self._process = await asyncio.create_subprocess_exec(
                *command,
                **kwargs,
            )
            logger.info("LSP server %r spawned (pid=%s).", self.name, self._process.pid)
        except (FileNotFoundError, OSError) as exc:
            await self._transition_to_error(
                LspInitializeError(
                    f"Failed to spawn LSP server {self.name!r}: {exc}"
                )
            )
            return

        # Start the stdout reader background task
        self._reader_task = asyncio.ensure_future(self._read_loop())
        # Start the stderr collector background task
        stderr_task = asyncio.ensure_future(self._collect_stderr())

        # Send the initialize request
        try:
            capabilities = await self._send_initialize()
            self.server_capabilities = capabilities or {}
            self._initialized = True

            # Send the initialized notification
            await self._send_notification(_METHOD_INITIALIZED, {})

            async with self._lock:
                self.state = "running"
                self.start_time = datetime.now(timezone.utc)
                self.restart_count = 0
            logger.info("LSP server %r initialized successfully.", self.name)
        except asyncio.TimeoutError:
            await self._cleanup_process()
            await self._transition_to_error(
                LspInitializeError(
                    f"LSP server {self.name!r} initialize timed out after "
                    f"{LSP_INITIALIZE_TIMEOUT}s"
                )
            )
            stderr_task.cancel()
        except Exception as exc:
            await self._cleanup_process()
            await self._transition_to_error(
                LspInitializeError(
                    f"LSP server {self.name!r} initialize failed: {exc}"
                )
            )
            stderr_task.cancel()

    async def stop(self) -> None:
        """Gracefully shut down the LSP server.

        Sends ``shutdown`` then ``exit``, kills the process if it does not
        exit promptly, and resolves all pending futures.

        Transitions: ``*`` → ``stopping`` → ``stopped``.
        """
        async with self._lock:
            if self.state in ("stopped", "stopping"):
                return
            self.state = "stopping"

        try:
            if self._process and self._process.returncode is None:
                # Graceful shutdown sequence
                try:
                    await asyncio.wait_for(
                        self._send_request(_METHOD_SHUTDOWN, {}),
                        timeout=LSP_SHUTDOWN_TIMEOUT,
                    )
                except (asyncio.TimeoutError, Exception):
                    logger.debug("LSP server %r shutdown timed out or failed; forcing.", self.name)

                # Always send exit notification (fire-and-forget)
                try:
                    await self._send_notification(_METHOD_EXIT, {})
                except Exception:
                    pass

                # Wait briefly for natural exit
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._cleanup_process()

        async with self._lock:
            self.state = "stopped"
            self.start_time = None
        logger.info("LSP server %r stopped.", self.name)

    async def restart(self) -> None:
        """Restart the LSP server with exponential backoff.

        After ``LSP_MAX_RESTART_COUNT`` consecutive failures the instance
        transitions to ``error`` and stops retrying.
        """
        self.restart_count += 1
        if self.restart_count > LSP_MAX_RESTART_COUNT:
            async with self._lock:
                self.state = "error"
                self.last_error = LspServerError(
                    f"LSP server {self.name!r} exceeded max restart count "
                    f"({LSP_MAX_RESTART_COUNT})"
                )
            logger.error("LSP server %r: max restart count exceeded.", self.name)
            return

        delay = min(
            LSP_RESTART_DELAY_BASE * (2 ** (self.restart_count - 1)),
            LSP_MAX_RESTART_DELAY,
        )
        logger.info(
            "LSP server %r restarting (attempt %s, delay %.2fs)...",
            self.name,
            self.restart_count,
            delay,
        )
        await asyncio.sleep(delay)

        # Stop gracefully (best-effort)
        await self._safe_stop()

        # Reset state for fresh start
        self._seq = 0
        self._pending.clear()

        await self.start()

    # -- health --------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Return True when the server is running and the process is alive."""
        if self.state != "running":
            return False
        if self._process is None:
            return False
        return self._process.returncode is None

    def get_state_info(self) -> LspServerStateInfo:
        """Return an immutable snapshot of the current state."""
        uptime: Optional[float] = None
        if self.start_time is not None and self.state == "running":
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        last_error_message = str(self.last_error) if self.last_error else ""
        return LspServerStateInfo(
            name=self.name,
            state=self.state,
            start_time=self.start_time,
            restart_count=self.restart_count,
            initialized=self._initialized,
            last_error_message=last_error_message,
            uptime_seconds=uptime,
        )

    # -- JSON-RPC messaging --------------------------------------------------

    async def send_request(self, method: str, params: Any) -> Any:
        """Send a JSON-RPC request to the server and wait for the response.

        Returns the ``result`` field of the response, or ``None`` on timeout.
        Raises ``LspTimeoutError`` if the server is not connected.
        """
        if not self._is_connected():
            raise LspTimeoutError(
                f"LSP server {self.name!r} is not connected (state={self.state})"
            )
        return await self._send_request(method, params, timeout=LSP_REQUEST_TIMEOUT)

    async def send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._is_connected():
            logger.debug(
                "LSP server %r not connected; dropping notification %r.",
                self.name,
                method,
            )
            return
        await self._send_notification(method, params)

    # -- handler registration ------------------------------------------------

    def on_notification(self, method: str, handler: Callable[[Any], None]) -> None:
        """Register a handler for server-to-client notifications.

        Handlers receive the ``params`` payload as a single argument.
        Multiple handlers can be registered per method.
        """
        self._notifications.setdefault(method, []).append(handler)

    def on_request(self, method: str, handler: Callable[[Any], Any]) -> None:
        """Register a handler for server-to-client requests.

        The handler receives ``params`` and must return a JSON-serialisable
        result.  Only one handler per method is supported.
        """
        self._request_handlers[method] = handler

    def remove_notification_handler(self, method: str, handler: Callable[[Any], None]) -> None:
        """Remove a previously registered notification handler."""
        handlers = self._notifications.get(method, [])
        if handler in handlers:
            handlers.remove(handler)
            if not handlers:
                self._notifications.pop(method, None)

    def remove_request_handler(self, method: str) -> None:
        """Remove the registered request handler for *method*."""
        self._request_handlers.pop(method, None)

    # ========================================================================
    # Internal: subprocess & transport
    # ========================================================================

    def _is_connected(self) -> bool:
        """True when the transport is ready for sending messages."""
        return (
            self._process is not None
            and self._process.returncode is None
            and self._process.stdin is not None
            and self._initialized
        )

    # -- initialize handshake ------------------------------------------------

    async def _send_initialize(self) -> dict[str, Any]:
        """Send the ``initialize`` request and return the server capabilities."""
        root_uri = self.config.root_uri or None
        params: dict[str, Any] = {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                    "completion": {
                        "dynamicRegistration": True,
                        "completionItem": {
                            "snippetSupport": True,
                            "documentationFormat": ["markdown", "plaintext"],
                        },
                    },
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "rename": {"dynamicRegistration": True},
                    "codeAction": {
                        "dynamicRegistration": True,
                        "codeActionLiteralSupport": {
                            "codeActionKind": {"valueSet": ["quickfix", "refactor", "source"]}
                        },
                    },
                    "diagnostic": {"dynamicRegistration": True},
                    "publishDiagnostics": {"relatedInformation": True},
                },
                "workspace": {
                    "workspaceFolders": True,
                    "configuration": True,
                    "symbol": {"dynamicRegistration": True},
                },
            },
        }
        if self.config.initialization_options:
            params["initializationOptions"] = self.config.initialization_options

        result = await self._send_request(
            _METHOD_INITIALIZE, params, timeout=LSP_INITIALIZE_TIMEOUT
        )
        if isinstance(result, dict):
            return result.get("capabilities") or {}
        return {}

    # -- low-level JSON-RPC send ---------------------------------------------

    async def _send_request(
        self,
        method: str,
        params: Any,
        timeout: float = LSP_REQUEST_TIMEOUT,
    ) -> Any:
        """Send a JSON-RPC request and wait for the matching response."""
        if not self._process or self._process.stdin is None:
            return None

        self._seq += 1
        seq = self._seq
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": seq,
            "method": method,
            "params": params,
        }
        data = json.dumps(msg, default=str).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8")

        try:
            self._process.stdin.write(header + data)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise LspProcessExitedError(
                f"LSP server {self.name!r} process write failed: {exc}"
            ) from exc

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[seq] = future

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            raise LspTimeoutError(
                f"LSP server {self.name!r} request {method!r} (id={seq}) timed out "
                f"after {timeout}s"
            )
        except asyncio.CancelledError:
            self._pending.pop(seq, None)
            raise

    async def _send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no ``id`` field)."""
        if not self._process or self._process.stdin is None:
            return

        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        data = json.dumps(msg, default=str).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8")

        try:
            self._process.stdin.write(header + data)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            logger.debug(
                "LSP server %r: failed to send notification %r: %s",
                self.name,
                method,
                exc,
            )

    # -- stdout reader loop --------------------------------------------------

    async def _read_loop(self) -> None:
        """Continuously read LSP messages from the server's stdout.

        Parses the ``Content-Length: N\\r\\n\\r\\n{json}`` framing, decodes
        the JSON body, and dispatches each message to the appropriate handler
        (response future or notification/request callback).

        On process exit or read error the task transitions the instance to
        ``error`` unless a deliberate shutdown is in progress.
        """
        if not self._process or not self._process.stdout:
            return

        buffer = b""
        try:
            while True:
                # Read next chunk
                try:
                    chunk = await self._process.stdout.read(LSP_READER_BUFFER_SIZE)
                except (OSError, ValueError) as exc:
                    logger.error("LSP server %r stdout read error: %s", self.name, exc)
                    break

                if not chunk:
                    # Process stdout closed
                    break

                buffer += chunk

                # Parse as many complete messages as possible from the buffer
                buffer = self._parse_messages_from_buffer(buffer)

            # If we exit the loop and the process has also exited, handle accordingly
            if self._process is not None:
                returncode = self._process.returncode
                if returncode is not None and self.state not in ("stopping", "stopped"):
                    # Unexpected exit
                    stderr_tail = "".join(self._stderr_buffer[-20:]) if self._stderr_buffer else ""
                    await self._transition_to_error(
                        LspProcessExitedError(
                            f"LSP server {self.name!r} exited unexpectedly with "
                            f"code {returncode}"
                            + (f"\nstderr: {stderr_tail}" if stderr_tail else "")
                        )
                    )
                elif returncode is not None and self.state == "stopping":
                    # Expected during shutdown
                    pass
                elif returncode is None and self.state not in ("stopping", "stopped"):
                    # Stdout closed but process still alive — unusual
                    await self._transition_to_error(
                        LspProcessExitedError(
                            f"LSP server {self.name!r} stdout closed but process still alive"
                        )
                    )

        except asyncio.CancelledError:
            # Reader task was cancelled (e.g. during stop)
            pass
        except Exception as exc:
            if self.state not in ("stopping", "stopped"):
                await self._transition_to_error(exc)

    def _parse_messages_from_buffer(self, buffer: bytes) -> bytes:
        """Extract zero or more complete LSP messages from *buffer*.

        Returns the remaining unconsumed bytes.
        """
        while True:
            # Look for Content-Length header
            header_end = buffer.find(b"\r\n\r\n")
            if header_end == -1:
                return buffer

            header_text = buffer[:header_end].decode("utf-8", errors="replace")
            content_length: Optional[int] = None
            for line in header_text.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    try:
                        content_length = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                    break

            if content_length is None:
                # Malformed header — cannot determine message boundaries.
                # Drain the entire buffer to prevent orphan bytes from
                # corrupting subsequent headers.  The next well-formed
                # message must start with a fresh Content-Length header.
                logger.warning(
                    "LSP server %r: message without Content-Length; "
                    "draining buffer (%d bytes) to resync.",
                    self.name,
                    len(buffer),
                )
                return b""

            body_start = header_end + 4
            if len(buffer) < body_start + content_length:
                # Incomplete body — wait for more data
                return buffer

            body_bytes = buffer[body_start : body_start + content_length]
            buffer = buffer[body_start + content_length :]

            # Parse and dispatch the message
            try:
                body_text = body_bytes.decode("utf-8")
                message = json.loads(body_text)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "LSP server %r: failed to decode message body: %s",
                    self.name,
                    exc,
                )
                continue

            self._dispatch_message(message)

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        """Route a parsed JSON-RPC message to the appropriate handler."""
        msg_id = message.get("id")

        if msg_id is not None and "method" not in message:
            # --- Response (has id, no method) ---
            future = self._pending.pop(msg_id, None)
            if future is not None and not future.done():
                if "error" in message:
                    error_obj = message["error"]
                    error_msg = error_obj.get("message", "Unknown LSP error")
                    error_code = error_obj.get("code", 0)
                    future.set_exception(
                        LspServerError(
                            f"LSP server {self.name!r} returned error "
                            f"({error_code}): {error_msg}"
                        )
                    )
                else:
                    future.set_result(message.get("result"))
            return

        method = message.get("method", "")

        if msg_id is not None:
            # --- Request from server (has id + method) ---
            handler = self._request_handlers.get(method)
            if handler is not None:
                try:
                    result = handler(message.get("params"))
                except Exception as exc:
                    logger.error(
                        "LSP server %r: request handler for %r failed: %s",
                        self.name, method, exc,
                    )
                    result = None
                # Send response back (best-effort)
                asyncio.ensure_future(
                    self._send_response(msg_id, result)
                )
            else:
                # No handler registered — respond with MethodNotFound
                asyncio.ensure_future(
                    self._send_error_response(
                        msg_id, -32601, f"Method not found: {method}"
                    )
                )
        else:
            # --- Notification from server (no id, has method) ---
            handlers = self._notifications.get(method, [])
            if not handlers:
                # Also try the wildcard handler
                handlers = self._notifications.get("*", [])
            params = message.get("params")
            for handler in handlers:
                try:
                    handler(params)
                except Exception:
                    logger.exception(
                        "LSP server %r: notification handler for %r failed.",
                        self.name,
                        method,
                    )

    # -- send responses to server requests -----------------------------------

    async def _send_response(self, request_id: Any, result: Any) -> None:
        """Send a success response for a server-to-client request."""
        if not self._process or self._process.stdin is None:
            return
        msg = {"jsonrpc": "2.0", "id": request_id, "result": result}
        data = json.dumps(msg, default=str).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8")
        try:
            self._process.stdin.write(header + data)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    async def _send_error_response(
        self, request_id: Any, code: int, message: str
    ) -> None:
        """Send an error response for a server-to-client request."""
        if not self._process or self._process.stdin is None:
            return
        msg = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        data = json.dumps(msg, default=str).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8")
        try:
            self._process.stdin.write(header + data)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # -- stderr collector ----------------------------------------------------

    async def _collect_stderr(self) -> None:
        """Collect stderr output from the server process for diagnostics."""
        if not self._process or not self._process.stderr:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                if decoded:
                    self._stderr_buffer.append(decoded)
                    # Keep buffer bounded
                    if len(self._stderr_buffer) > 200:
                        self._stderr_buffer = self._stderr_buffer[-100:]
        except (asyncio.CancelledError, OSError):
            pass

    def get_stderr_log(self, tail_lines: int = 50) -> list[str]:
        """Return the last *tail_lines* lines of server stderr output."""
        return self._stderr_buffer[-tail_lines:]

    # -- cleanup -------------------------------------------------------------

    async def _cleanup_process(self) -> None:
        """Cancel reader/stderr tasks, kill process, resolve pending futures."""
        # Cancel the reader task
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None

        # Kill and reap the process
        if self._process is not None:
            if self._process.returncode is None:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass  # already dead
                except Exception:
                    pass
            try:
                await self._process.wait()
            except Exception:
                pass
            self._process = None

        # Resolve all pending futures with an error
        pending = dict(self._pending)
        self._pending.clear()
        for future in pending.values():
            if not future.done():
                future.set_exception(
                    LspProcessExitedError(
                        f"LSP server {self.name!r} was shut down"
                    )
                )

    async def _safe_stop(self) -> None:
        """Best-effort stop that never raises."""
        try:
            await self.stop()
        except Exception:
            pass

    # -- state helpers -------------------------------------------------------

    async def _transition_to_error(self, error: BaseException) -> None:
        """Transition the instance to the ``error`` state."""
        async with self._lock:
            self.state = "error"
            self.last_error = error
        logger.error("LSP server %r error: %s", self.name, error)
        # Resolve any pending futures
        self._cancel_pending(error)

    def _cancel_pending(self, error: BaseException) -> None:
        """Cancel all pending request futures with *error*."""
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_lsp_server_instance(
    name: str,
    config: ScopedLspServerConfig,
    _create_client: Callable[..., Any] | None = None,
) -> LspServerInstance:
    """Create an ``LspServerInstance``.

    The *_create_client* parameter is reserved for dependency injection
    of a custom protocol client (e.g. ``LSPClient``).  When ``None`` the
    instance handles JSON-RPC internally.
    """
    return LspServerInstance(name=name, config=config)
