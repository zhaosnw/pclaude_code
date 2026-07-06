"""
OAuth loopback redirect port selection, allocation, and callback server.

Port of: src/services/mcp/oauthPort.ts

Provides port discovery, allocation tracking, redirect URI construction,
and an asyncio-based loopback HTTP server for receiving OAuth authorization
code callbacks (RFC 8252 Section 7.3).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("hare.mcp.oauth_port")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDIRECT_PORT_FALLBACK = 3118

# Windows dynamic port range 49152-65535 is reserved; use the lower range.
_REDIRECT_RANGE_WINDOWS = (39152, 49151)
_REDIRECT_RANGE_DEFAULT = (49152, 65535)

# Maximum port-finding attempts before falling back.
_MAX_PORT_ATTEMPTS = 100

# Timeout for the loopback callback server (5 minutes per the TS source).
_CALLBACK_TIMEOUT_SECONDS = 5 * 60

# How long to hold a port reservation before releasing (seconds).
_PORT_RESERVATION_TTL = 300

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PortUnavailableError(RuntimeError):
    """Raised when no available port could be found for the OAuth callback."""

    def __init__(self, message: str = "No available ports for OAuth redirect") -> None:
        super().__init__(message)


class CallbackTimeoutError(RuntimeError):
    """Raised when the OAuth callback does not arrive within the timeout."""


class CallbackStateMismatchError(ValueError):
    """Raised when the OAuth state parameter does not match (possible CSRF)."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PortAllocation:
    """Result of a successful port allocation for an OAuth loopback server."""

    port: int
    redirect_uri: str
    host: str = "127.0.0.1"
    allocated_at: float = field(default_factory=time.monotonic)
    was_configured: bool = False
    attempt_count: int = 1

    @property
    def callback_url(self) -> str:
        """The full callback URL (alias for redirect_uri)."""
        return self.redirect_uri

    @property
    def age_seconds(self) -> float:
        """Seconds since this port was allocated."""
        return time.monotonic() - self.allocated_at


@dataclass
class OAuthCallbackResult:
    """Result from a completed OAuth callback on the loopback server."""

    code: str
    state: str
    redirect_uri: str
    received_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _redirect_range() -> tuple[int, int]:
    """Return the acceptable ephemeral port range for the current platform."""
    if sys.platform == "win32":
        return _REDIRECT_RANGE_WINDOWS
    return _REDIRECT_RANGE_DEFAULT


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether *port* is free to bind on *host*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _configured_port() -> int | None:
    """Read MCP_OAUTH_CALLBACK_PORT from the environment, if set and valid."""
    raw = os.environ.get("MCP_OAUTH_CALLBACK_PORT", "")
    if not raw:
        return None
    try:
        p = int(raw, 10)
        return p if 1 <= p <= 65535 else None
    except ValueError:
        logger.debug("MCP_OAUTH_CALLBACK_PORT is not a valid integer: %r", raw)
        return None


# ---------------------------------------------------------------------------
# Public API – redirect URI
# ---------------------------------------------------------------------------


def build_redirect_uri(port: int = REDIRECT_PORT_FALLBACK, host: str = "127.0.0.1") -> str:
    """Build a loopback redirect URI for OAuth (RFC 8252 Section 7.3).

    Args:
        port: The port to listen on.
        host: The loopback host (default 127.0.0.1).

    Returns:
        A redirect URI like ``http://127.0.0.1:{port}/callback``.
    """
    return f"http://{host}:{port}/callback"


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


async def find_available_port(
    preferred_port: int | None = None,
    host: str = "127.0.0.1",
) -> int:
    """Find an available port for the OAuth loopback callback.

    Resolution order:
    1. Explicit *preferred_port* (e.g. from server config ``oauth.callbackPort``).
    2. ``MCP_OAUTH_CALLBACK_PORT`` environment variable.
    3. Random probe within the platform-appropriate ephemeral range.
    4. ``REDIRECT_PORT_FALLBACK`` (3118).

    Raises:
        PortUnavailableError: If no port could be acquired after exhausting
            all strategies.
    """
    # Strategy 1: explicit preferred port (caller-supplied).
    if preferred_port is not None:
        if _port_available(preferred_port, host):
            logger.debug("Using preferred port %d (caller-supplied).", preferred_port)
            return preferred_port
        logger.warning(
            "Preferred port %d is unavailable; falling back to discovery.",
            preferred_port,
        )

    # Strategy 2: environment variable.
    configured = _configured_port()
    if configured is not None:
        if _port_available(configured, host):
            logger.debug("Using configured port %d (MCP_OAUTH_CALLBACK_PORT).", configured)
            return configured
        logger.warning(
            "Configured port %d from MCP_OAUTH_CALLBACK_PORT is unavailable.",
            configured,
        )

    # Strategy 3: random probe within the platform range.
    min_p, max_p = _redirect_range()
    span = max_p - min_p + 1
    attempts = min(span, _MAX_PORT_ATTEMPTS)

    tried: set[int] = set()
    for attempt in range(1, attempts + 1):
        # Pick a random port not yet tried.
        while True:
            port = min_p + random.randint(0, span - 1)
            if port not in tried:
                tried.add(port)
                break
            if len(tried) >= span:
                break

        if _port_available(port, host):
            logger.debug(
                "Found available port %d on attempt %d/%d.",
                port, attempt, attempts,
            )
            return port

        # Brief yield to avoid tight-looping on a busy system.
        if attempt % 10 == 0:
            await asyncio.sleep(0)

    # Strategy 4: fallback port.
    if _port_available(REDIRECT_PORT_FALLBACK, host):
        logger.info("Falling back to default port %d.", REDIRECT_PORT_FALLBACK)
        return REDIRECT_PORT_FALLBACK

    raise PortUnavailableError(
        f"No available ports for OAuth redirect "
        f"(tried {len(tried)} random ports + fallback {REDIRECT_PORT_FALLBACK})"
    )


async def allocate_port(
    preferred_port: int | None = None,
    host: str = "127.0.0.1",
) -> PortAllocation:
    """Allocate a port and return a structured :class:`PortAllocation`.

    This wraps :func:`find_available_port` with additional metadata about
    how the port was obtained.
    """
    t0 = time.monotonic()
    attempt_count = 1

    # Determine if a configured/preferred port was used.
    configured = _configured_port()
    explicit = preferred_port

    port = await find_available_port(preferred_port=preferred_port, host=host)

    was_configured = (explicit is not None and port == explicit) or (
        configured is not None and port == configured and explicit is None
    )

    alloc = PortAllocation(
        port=port,
        redirect_uri=build_redirect_uri(port, host),
        host=host,
        allocated_at=time.monotonic(),
        was_configured=was_configured,
        attempt_count=attempt_count,
    )
    logger.info(
        "Allocated loopback port %d (configured=%s, elapsed=%.3fs).",
        port, was_configured, time.monotonic() - t0,
    )
    return alloc


# ---------------------------------------------------------------------------
# Loopback callback server
# ---------------------------------------------------------------------------


def _html_page(title: str, body: str, success: bool = True) -> bytes:
    """Render a minimal HTML response page for the browser callback."""
    color = "#16a34a" if success else "#dc2626"
    return (
        f"<html><head><meta charset=\"utf-8\"><title>{title}</title></head>"
        f"<body style=\"font-family:system-ui,sans-serif;display:flex;"
        f"align-items:center;justify-content:center;height:100vh;margin:0\">"
        f"<div style=\"text-align:center\">"
        f"<h2 style=\"color:{color}\">{title}</h2><p>{body}</p>"
        f"<p style=\"color:#6b7280;font-size:0.875rem\">You can close this window.</p>"
        f"</div></body></html>"
    ).encode("utf-8")


async def _read_http_request(
    reader: asyncio.StreamReader, timeout: float = 5.0
) -> bytes:
    """Read an HTTP request from *reader* into a byte string."""
    data = bytearray()
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                break
            data.extend(chunk)
            if b"\r\n\r\n" in data:
                break
    except asyncio.TimeoutError:
        pass
    return bytes(data)


def _parse_callback_path(request_data: bytes) -> dict[str, list[str]]:
    """Extract query parameters from the callback request path."""
    try:
        first_line = request_data.split(b"\r\n")[0].decode("utf-8", errors="replace")
    except IndexError:
        return {}
    # first_line looks like: GET /callback?code=...&state=... HTTP/1.1
    parts = first_line.split()
    if len(parts) < 2:
        return {}
    path_and_query = parts[1]
    parsed = urlparse(path_and_query)
    if parsed.path != "/callback":
        return {}
    return parse_qs(parsed.query)


async def start_loopback_callback_server(
    port: int,
    expected_state: str,
    host: str = "127.0.0.1",
    timeout: float = _CALLBACK_TIMEOUT_SECONDS,
    on_listening: Callable[[PortAllocation], Awaitable[None]] | None = None,
    on_manual_callback: Callable[[str], None] | None = None,
) -> OAuthCallbackResult:
    """Start an asyncio-based loopback HTTP server and wait for the OAuth callback.

    The server listens on ``http://{host}:{port}/callback`` and resolves when
    the authorization provider redirects the browser back to it.  The
    *expected_state* parameter is compared against the ``state`` query parameter
    to prevent CSRF attacks.

    Args:
        port: Port to listen on.
        expected_state: The OAuth ``state`` value to validate against.
        host: Loopback host (default ``127.0.0.1``).
        timeout: Maximum time to wait for the callback (seconds).
        on_listening: Optional async callback invoked once the server socket
            is bound.  Receives a :class:`PortAllocation`.  Use this to open
            the browser at the right moment (after the port is confirmed bound).
        on_manual_callback: Optional sync callback for manual callback URL entry.
            Called with the callback URL string so the user can paste it.

    Returns:
        :class:`OAuthCallbackResult` with the authorization code and state.

    Raises:
        PortUnavailableError: If the port cannot be bound.
        CallbackTimeoutError: If the callback does not arrive within *timeout*.
        CallbackStateMismatchError: If the ``state`` parameter does not match.
        ValueError: If the authorization server returns an error.
    """
    alloc = PortAllocation(
        port=port,
        redirect_uri=build_redirect_uri(port, host),
        host=host,
    )

    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal resolved
        if resolved:
            writer.close()
            await writer.wait_closed()
            return

        request_data = await _read_http_request(reader)
        params = _parse_callback_path(request_data)

        if not params:
            # Not a /callback request — 404.
            writer.write(
                b"HTTP/1.1 404 Not Found\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
            writer.close()
            await writer.wait_closed()
            return

        # Check for OAuth error response.
        error_vals = params.get("error", [])
        if error_vals:
            error = error_vals[0]
            error_desc = params.get("error_description", [None])[0]
            desc = error_desc or ""
            body = _html_page(
                "Authentication Error",
                f"{error}: {desc}",
                success=False,
            )
            writer.write(
                f"HTTP/1.1 400 Bad Request\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
                .encode("utf-8") + body
            )
            writer.close()
            await writer.wait_closed()
            msg = f"OAuth error: {error}"
            if desc:
                msg += f" - {desc}"
            error_uri = params.get("error_uri", [None])[0]
            if error_uri:
                msg += f" (See: {error_uri})"
            result_error = ValueError(msg)
            result_error = result_error  # captured in closure
            if not resolved:
                resolved = True
                server.close()
                fut.set_exception(ValueError(msg))
            return

        # Validate state for CSRF protection.
        state_vals = params.get("state", [])
        received_state = state_vals[0] if state_vals else ""
        if received_state != expected_state:
            body = _html_page(
                "Authentication Error",
                "Invalid state parameter. Please try again.",
                success=False,
            )
            writer.write(
                f"HTTP/1.1 400 Bad Request\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
                .encode("utf-8") + body
            )
            writer.close()
            await writer.wait_closed()
            if not resolved:
                resolved = True
                server.close()
                fut.set_exception(
                    CallbackStateMismatchError(
                        "OAuth state mismatch - possible CSRF attack"
                    )
                )
            return

        # Success — extract authorization code.
        code_vals = params.get("code", [])
        if not code_vals:
            writer.close()
            await writer.wait_closed()
            return  # ignore requests without a code (browser pre-flight, etc.)

        code = code_vals[0]
        body = _html_page(
            "Authentication Successful",
            "Return to Claude Code.",
            success=True,
        )
        writer.write(
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
            .encode("utf-8") + body
        )
        writer.close()
        await writer.wait_closed()

        if not resolved:
            resolved = True
            server.close()
            logger.debug(
                "OAuth callback received: code=<redacted>, state matched."
            )
            fut.set_result(
                OAuthCallbackResult(
                    code=code,
                    state=received_state,
                    redirect_uri=alloc.redirect_uri,
                )
            )

    fut: asyncio.Future[OAuthCallbackResult] = asyncio.Future()
    resolved = False

    try:
        server = await asyncio.start_server(
            handle_client,
            host=host,
            port=port,
        )
    except OSError as exc:
        raise PortUnavailableError(
            f"Cannot bind OAuth callback server to {host}:{port}: {exc}"
        ) from exc

    try:
        async with server:
            logger.info(
                "OAuth loopback server listening on %s.", alloc.redirect_uri
            )

            # Notify the caller that the port is ready — they can open the browser now.
            if on_listening is not None:
                try:
                    await on_listening(alloc)
                except Exception:
                    logger.exception("on_listening callback raised an exception.")

            # Set up manual callback URL handler if provided.
            if on_manual_callback is not None:

                def _manual_submit(callback_url: str) -> None:
                    """Process a manually pasted callback URL."""
                    if resolved:
                        return
                    try:
                        parsed = urlparse(callback_url)
                        qs = parse_qs(parsed.query)
                        error_vals = qs.get("error", [])
                        if error_vals:
                            desc = qs.get("error_description", [None])[0]
                            msg = f"OAuth error: {error_vals[0]}"
                            if desc:
                                msg += f" - {desc}"
                            if not resolved:
                                resolved = True
                                server.close()
                                fut.set_exception(ValueError(msg))
                            return
                        code_vals = qs.get("code", [])
                        state_vals = qs.get("state", [])
                        if not code_vals:
                            return
                        if state_vals and state_vals[0] != expected_state:
                            if not resolved:
                                resolved = True
                                server.close()
                                fut.set_exception(
                                    CallbackStateMismatchError(
                                        "OAuth state mismatch - possible CSRF attack"
                                    )
                                )
                            return
                        if not resolved:
                            resolved = True
                            server.close()
                            fut.set_result(
                                OAuthCallbackResult(
                                    code=code_vals[0],
                                    state=state_vals[0] if state_vals else expected_state,
                                    redirect_uri=alloc.redirect_uri,
                                )
                            )
                    except Exception:
                        pass  # Ignore invalid URLs so the user can retry.

                # Pass the submit function to the caller so they can trigger it.
                on_manual_callback(_manual_submit)  # type: ignore[arg-type]

            # Wait for callback or timeout.
            try:
                result = await asyncio.wait_for(fut, timeout=timeout)
                logger.info("OAuth callback completed successfully.")
                return result
            except asyncio.TimeoutError:
                if not resolved:
                    resolved = True
                    server.close()
                    raise CallbackTimeoutError(
                        f"OAuth callback timed out after {timeout:.0f}s"
                    )
                # If resolved in the meantime, return whatever is in the future.
                if fut.done() and not fut.cancelled():
                    return fut.result()
                raise

    # Ensure server is stopped on any exit path.
    finally:
        if not resolved:
            resolved = True
            if not fut.done():
                fut.cancel()


# ---------------------------------------------------------------------------
# Convenience – combined allocate + listen
# ---------------------------------------------------------------------------


async def allocate_and_listen(
    expected_state: str,
    preferred_port: int | None = None,
    host: str = "127.0.0.1",
    timeout: float = _CALLBACK_TIMEOUT_SECONDS,
    on_listening: Callable[[PortAllocation], Awaitable[None]] | None = None,
    on_manual_callback: Callable[[str], None] | None = None,
) -> tuple[PortAllocation, OAuthCallbackResult]:
    """Allocate a port and start the loopback callback server in one call.

    Returns a tuple of (:class:`PortAllocation`, :class:`OAuthCallbackResult`).
    """
    alloc = await allocate_port(preferred_port=preferred_port, host=host)
    result = await start_loopback_callback_server(
        port=alloc.port,
        expected_state=expected_state,
        host=host,
        timeout=timeout,
        on_listening=on_listening,
        on_manual_callback=on_manual_callback,
    )
    return alloc, result
