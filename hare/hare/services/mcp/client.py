"""
MCP client connection management and tool calling.

Port of: src/services/mcp/client.ts

Implements JSON-RPC over stdio and SSE/HTTP transports for MCP server
communication. Supports connection lifecycle (pending→connected/failed),
tool listing with TTL caching, tool calling with result handling,
LRU-memoized reflection, automatic reconnection with exponential backoff,
health checks, and full SSE streaming transport for remote servers.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import random
import subprocess
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from hare.services.mcp.types import (
    ConnectionStatus,
    MCPServerConnection,
    McpServerConfig,
    McpStdioServerConfig,
    McpSseServerConfig,
    McpToolInfo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON-RPC types
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
INIT_TIMEOUT = 30.0
TOOL_CALL_TIMEOUT = 120.0


@dataclass
class _PendingRequest:
    future: asyncio.Future[dict[str, Any]]
    method: str


@dataclass
class StdioSession:
    """Active JSON-RPC session over a subprocess stdio pipe."""

    server_name: str
    process: subprocess.Popen[bytes]
    request_id: int = 0
    pending: dict[int, _PendingRequest] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    server_info: dict[str, Any] = field(default_factory=dict)
    reader_task: asyncio.Task[None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _closed: bool = False

    def next_id(self) -> int:
        self.request_id += 1
        return self.request_id


# ---------------------------------------------------------------------------
# LRU cache with TTL support (matching TS LRU memoization with expiry)
# ---------------------------------------------------------------------------


class LRUCache:
    """Simple LRU cache matching TS LRU memoization pattern."""

    def __init__(self, max_size: int = 20) -> None:
        self._max = max_size
        self._data: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def set(self, key: str, value: Any) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


@dataclass
class _CacheEntry:
    """Single cache entry with expiry timestamp."""

    value: Any
    expires_at: float = 0.0  # 0 = never expires


class TTLCache(LRUCache):
    """LRU cache with per-entry TTL expiry.

    Extends LRUCache so all existing get/set calls work unmodified.
    TTL-expired entries are evicted lazily on access.
    """

    _NO_EXPIRE = 0.0

    def __init__(self, max_size: int = 20, default_ttl: float = 300.0) -> None:
        super().__init__(max_size)
        self._default_ttl = default_ttl
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        if self._is_expired(entry):
            self._entries.pop(key, None)
            self._evictions += 1
            self._misses += 1
            return None
        self._entries.move_to_end(key)
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.monotonic() + effective_ttl if effective_ttl > 0 else self._NO_EXPIRE
        if key in self._entries:
            self._entries.move_to_end(key)
        self._entries[key] = _CacheEntry(value=value, expires_at=expires_at)
        while len(self._entries) > self._max:
            removed_key, _ = self._entries.popitem(last=False)
            self._evictions += 1

    def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def evict_expired(self) -> int:
        """Eagerly evict all expired entries. Returns count of evictions."""
        now = time.monotonic()
        expired_keys = [
            k for k, e in self._entries.items() if self._is_expired(e, now=now)
        ]
        for k in expired_keys:
            self._entries.pop(k, None)
        self._evictions += len(expired_keys)
        return len(expired_keys)

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "evictions": self._evictions}

    @staticmethod
    def _is_expired(entry: _CacheEntry, *, now: float | None = None) -> bool:
        if entry.expires_at <= 0:
            return False
        return (now if now is not None else time.monotonic()) >= entry.expires_at


# ---------------------------------------------------------------------------
# Reconnection / retry configuration
# ---------------------------------------------------------------------------


@dataclass
class ReconnectionConfig:
    """Exponential-backoff reconnection settings."""

    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    health_check_interval: float = 30.0  # seconds between pings
    health_check_timeout: float = 10.0


def _backoff_delay(attempt: int, config: ReconnectionConfig) -> float:
    """Compute exponential backoff delay with optional jitter.

    port of TS exponential-backoff pattern with full jitter.
    """
    delay = min(config.base_delay * (config.backoff_multiplier ** attempt), config.max_delay)
    if config.jitter:
        delay = random.uniform(0, delay)
    return delay


async def _retry_with_backoff(
    op_name: str,
    server_name: str,
    fn,
    config: ReconnectionConfig,
    *,
    ignore_exceptions: tuple[type[Exception], ...] = (),
) -> Any:
    """Execute an async function with exponential backoff retries.

    Args:
        op_name: Human-readable operation name for logging.
        server_name: MCP server name for log context.
        fn: Async callable to retry.
        config: Backoff configuration.
        ignore_exceptions: Exception types that should NOT trigger retry.

    Returns the result of the successful call.

    Raises the last exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(config.max_retries + 1):
        try:
            return await fn()
        except ignore_exceptions:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt >= config.max_retries:
                break
            delay = _backoff_delay(attempt, config)
            logger.warning(
                "MCP %s for '%s' failed (attempt %d/%d): %s. Retrying in %.1fs",
                op_name,
                server_name,
                attempt + 1,
                config.max_retries + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    logger.error(
        "MCP %s for '%s' exhausted all %d retries: %s",
        op_name,
        server_name,
        config.max_retries + 1,
        last_exc,
    )
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# SSE transport (async streaming client for remote MCP servers)
# ---------------------------------------------------------------------------


class SSEClientTransport:
    """Async SSE transport for remote MCP servers.

    Implements the SSE transport spec from @modelcontextprotocol/sdk:
    - Opens a long-lived GET connection to the server's SSE endpoint.
    - Parses the SSE event stream: event types, data, and the 'endpoint'
      event that tells us where to POST JSON-RPC requests.
    - Sends JSON-RPC requests via HTTP POST to the discovered endpoint.
    - Handles reconnection on stream failure with backoff.
    """

    _READ_CHUNK = 8192
    _CONNECT_TIMEOUT = 30.0

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        reconnection: ReconnectionConfig | None = None,
    ) -> None:
        self._url = url.rstrip("/") if url else ""
        self._headers = dict(headers) if headers else {}
        self._headers.setdefault("Accept", "text/event-stream")
        self._headers.setdefault("Cache-Control", "no-cache")
        self._reconnection = reconnection or ReconnectionConfig()
        self._endpoint_url: str = ""  # discovered via 'endpoint' SSE event
        self._response_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._reader_task: asyncio.Task[None] | None = None
        self._closed: bool = False
        self._request_id: int = 0

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the SSE stream and discover the POST endpoint."""
        self._closed = False

        loop = asyncio.get_running_loop()

        # Parse URL components for the SSE GET request
        from urllib.parse import urlparse

        parsed = urlparse(self._url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        use_tls = parsed.scheme == "https"

        logger.debug("SSE connecting to %s:%d%s (tls=%s)", host, port, path, use_tls)

        # Open an async HTTP connection and start reading the SSE stream.
        # We use run_in_executor with a sync HTTP client for the long-lived
        # connection because Python's built-in asyncio HTTP is limited.
        # Production deployments should use aiohttp; this keeps zero deps.
        def _open_stream() -> http.client.HTTPResponse:
            if use_tls:
                conn = http.client.HTTPSConnection(host, port, timeout=self._CONNECT_TIMEOUT)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=self._CONNECT_TIMEOUT)
            conn.putrequest("GET", path)
            for k, v in self._headers.items():
                conn.putheader(k, v)
            conn.endheaders()
            resp = conn.getresponse()
            if resp.status not in (200, 301, 302, 307, 308):
                body = resp.read(4096).decode("utf-8", errors="replace")
                raise McpError(
                    code=-32000,
                    message=f"SSE connection failed: HTTP {resp.status} {resp.reason} — {body[:500]}",
                )
            content_type = resp.getheader("Content-Type", "")
            if "text/event-stream" not in content_type:
                logger.warning(
                    "SSE endpoint returned Content-Type %r instead of text/event-stream",
                    content_type,
                )
            return resp

        try:
            response = await loop.run_in_executor(None, _open_stream)
        except OSError as exc:
            raise McpError(code=-32000, message=f"SSE connection to {self._url} failed: {exc}") from exc

        self._reader_task = asyncio.ensure_future(self._read_sse_stream(response))

        # Wait for the 'endpoint' event (required by MCP SSE spec)
        try:
            endpoint_msg = await asyncio.wait_for(
                self._wait_for_event("endpoint"),
                timeout=self._CONNECT_TIMEOUT,
            )
            discovered_path = endpoint_msg.get("data", "")
            if discovered_path:
                # Resolve relative URL against the base SSE URL
                from urllib.parse import urljoin

                self._endpoint_url = urljoin(self._url, discovered_path)
                logger.debug("SSE discovered POST endpoint: %s", self._endpoint_url)
            else:
                # Fallback: POST to the same URL
                self._endpoint_url = self._url
        except asyncio.TimeoutError:
            logger.warning(
                "SSE did not receive 'endpoint' event within %ss, using base URL for POST",
                self._CONNECT_TIMEOUT,
            )
            self._endpoint_url = self._url

    async def close(self) -> None:
        """Close the SSE stream and clean up."""
        self._closed = True
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        # Drain response queue
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ------------------------------------------------------------------
    # JSON-RPC over SSE (POST requests, SSE responses)
    # ------------------------------------------------------------------

    async def send_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request via HTTP POST and wait for the SSE response."""
        if self._closed:
            raise McpError(code=-32000, message="SSE transport is closed")

        self._request_id += 1
        req_id = self._request_id
        payload = json.dumps(
            {
                "jsonrpc": JSONRPC_VERSION,
                "id": req_id,
                "method": method,
                "params": params,
            }
        )

        # POST the request
        await self._http_post(self._endpoint_url or self._url, payload)

        # Wait for the matching response on the SSE event stream
        try:
            response = await asyncio.wait_for(
                self._wait_for_response(req_id),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise McpError(
                code=-32000,
                message=f"SSE request timed out after {timeout}s: {method}",
            )

        if "error" in response:
            err = response["error"]
            raise McpError(
                code=err.get("code", -1),
                message=err.get("message", "Unknown MCP error"),
            )

        return response.get("result", response)

    async def send_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        """Send a JSON-RPC notification via HTTP POST (fire-and-forget)."""
        if self._closed:
            return
        payload = json.dumps(
            {
                "jsonrpc": JSONRPC_VERSION,
                "method": method,
                "params": params,
            }
        )
        try:
            await self._http_post(self._endpoint_url or self._url, payload)
        except Exception:
            logger.debug("SSE notification %s failed (ignored)", method, exc_info=True)

    # ------------------------------------------------------------------
    # Internal: SSE stream reader
    # ------------------------------------------------------------------

    async def _read_sse_stream(self, response: http.client.HTTPResponse) -> None:
        """Continuously read the SSE stream and enqueue events.

        Parses the SSE wire format:
            event: <type>
            data: <json>\n\n
        """
        loop = asyncio.get_running_loop()

        def _read_chunk() -> bytes | None:
            try:
                return response.read(self._READ_CHUNK)
            except Exception:
                return None

        buffer = b""
        try:
            while not self._closed:
                chunk = await loop.run_in_executor(None, _read_chunk)
                if not chunk:
                    logger.warning("SSE stream closed by server")
                    break
                buffer += chunk
                buffer = self._process_sse_buffer(buffer)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if not self._closed:
                logger.error("SSE stream read error: %s", exc)
        finally:
            try:
                response.close()
            except Exception:
                pass

    def _process_sse_buffer(self, buffer: bytes) -> bytes:
        """Extract complete SSE events from the byte buffer.

        SSE events are separated by double newlines (\\n\\n).
        Each event may have event: and data: fields.
        Returns the remaining unconsumed buffer.
        """
        while b"\n\n" in buffer:
            raw_event, buffer = buffer.split(b"\n\n", 1)
            try:
                self._parse_sse_event(raw_event.decode("utf-8", errors="replace"))
            except ValueError:
                pass
        return buffer

    def _parse_sse_event(self, raw: str) -> None:
        """Parse a single SSE event from its text representation.

        Format:
            event: <event_type>
            data: <json_payload>
            id: <event_id>
        """
        event_type = "message"
        data_parts: list[str] = []

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(":"):
                continue  # SSE comment
            if ":" in line:
                field, _, value = line.partition(":")
                field = field.strip()
                value = value.strip()
                if field == "event":
                    event_type = value
                elif field == "data":
                    data_parts.append(value)
                # id: and retry: are ignored for now
            else:
                # Line without colon: treat as data without field name
                data_parts.append(line)

        data_str = "\n".join(data_parts)
        try:
            data_parsed = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            data_parsed = data_str

        # Enqueue the event — drop if queue is full (non-blocking)
        try:
            self._response_queue.put_nowait({"event": event_type, "data": data_parsed})
        except asyncio.QueueFull:
            logger.warning("SSE response queue full, dropping event type=%s", event_type)

    # ------------------------------------------------------------------
    # Internal: wait for specific SSE events
    # ------------------------------------------------------------------

    async def _wait_for_event(self, event_type: str) -> dict[str, Any]:
        """Read from the SSE queue until we get an event of the given type."""
        while True:
            msg = await self._response_queue.get()
            if msg.get("event") == event_type:
                return msg

    async def _wait_for_response(self, req_id: int) -> dict[str, Any]:
        """Read SSE events until we get a JSON-RPC response matching req_id."""
        while True:
            msg = await self._response_queue.get()
            data = msg.get("data")
            if isinstance(data, dict) and data.get("id") == req_id:
                return data
            # Non-matching messages are dropped; responses always have an 'id'

    # ------------------------------------------------------------------
    # Internal: HTTP POST helper
    # ------------------------------------------------------------------

    async def _http_post(self, url: str, payload: str) -> None:
        """POST a JSON-RPC payload to the MCP server endpoint."""
        loop = asyncio.get_running_loop()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }

        def _post() -> None:
            data = payload.encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                urllib.request.urlopen(req, timeout=30)
            except urllib.error.HTTPError as exc:
                logger.warning("SSE POST returned HTTP %d for %s", exc.code, url)

        await loop.run_in_executor(None, _post)

    @property
    def is_connected(self) -> bool:
        return not self._closed and self._reader_task is not None and not self._reader_task.done()


# ---------------------------------------------------------------------------
# Remote session (SSE/HTTP transport wrapper for McpClientPool)
# ---------------------------------------------------------------------------


@dataclass
class RemoteSession:
    """Active JSON-RPC session over an SSE transport (remote MCP server).

    Counterpart to StdioSession for non-stdio transports.
    """

    server_name: str
    transport: SSEClientTransport
    capabilities: dict[str, Any] = field(default_factory=dict)
    server_info: dict[str, Any] = field(default_factory=dict)
    _closed: bool = False


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------


@dataclass
class McpClientPool:
    """Manages MCP server connections with caching and lifecycle.

    Connection state machine (matching TS MCP server connection discriminator):
    - pending: initial state or reconnecting
    - connected: live connection with capabilities
    - failed: connection attempt failed, holds error message
    - needs_auth: remote server returned 401, needs OAuth
    - disabled: user disabled the server
    """

    sessions: dict[str, StdioSession | RemoteSession] = field(default_factory=dict)
    connection_results: dict[str, MCPServerConnection] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _tools_cache: TTLCache = field(default_factory=lambda: TTLCache(max_size=20, default_ttl=300.0))
    _commands_cache: TTLCache = field(default_factory=lambda: TTLCache(max_size=20, default_ttl=300.0))
    _resources_cache: TTLCache = field(default_factory=lambda: TTLCache(max_size=20, default_ttl=300.0))
    _reconnection_config: ReconnectionConfig = field(default_factory=ReconnectionConfig)
    _health_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Connect
    # ------------------------------------------------------------------

    async def connect(self, name: str, config: McpServerConfig) -> MCPServerConnection:
        """Connect to an MCP server, returning its connection state.

        Connection state machine: pending → connected | failed | needs_auth.
        """
        async with self._lock:
            existing = self.sessions.get(name)
            if existing is not None and not existing._closed:
                return self._build_connected(name, config, existing, "connected")

        try:
            session = await self._connect_transport(name, config)
        except Exception as exc:
            return MCPServerConnection(
                name=name,
                config=config,
                status="failed",
                connected=False,
                error=str(exc),
            )

        async with self._lock:
            self.sessions[name] = session

        conn = self._build_connected(name, config, session, "connected")

        # Fetch tools, commands, resources (matching TS getMcpToolsCommandsAndResources)
        try:
            tools = await self.list_tools(name)
            conn.tools = tools
            conn.mcp_tools = [_wrap_tool(name, t) for t in tools]
        except Exception:
            pass

        async with self._lock:
            self.connection_results[name] = conn

        return conn

    async def ensure_connected_client(self, name: str) -> MCPServerConnection:
        """Reuse cached connection or reconnect. Matching TS ensureConnectedClient."""
        async with self._lock:
            existing = self.connection_results.get(name)
            if existing is not None and existing.status == "connected":
                session = self.sessions.get(name)
                if session is not None and not session._closed:
                    return existing
        # If not connected, raise (caller should reconnect)
        raise MCPError(code=-32000, message=f"Not connected to MCP server: {name}")

    async def reconnect(
        self, name: str, config: McpServerConfig
    ) -> MCPServerConnection:
        """Clear caches and reconnect. Matching TS reconnectMcpServerImpl."""
        # Clear caches
        self._tools_cache.delete(name)
        self._commands_cache.delete(name)
        self._resources_cache.delete(name)

        # Disconnect existing
        await self.disconnect(name)

        # Reconnect
        return await self.connect(name, config)

    async def reconnect_with_backoff(
        self, name: str, config: McpServerConfig
    ) -> MCPServerConnection:
        """Reconnect with exponential backoff. Useful for transient failures.

        Clears caches, disconnects, and retries connection with backoff.
        Returns the new connection state (connected or failed).
        """
        self._tools_cache.delete(name)
        self._commands_cache.delete(name)
        self._resources_cache.delete(name)
        await self.disconnect(name)

        last_exc: Exception | None = None
        for attempt in range(self._reconnection_config.max_retries + 1):
            try:
                result = await self.connect(name, config)
                if result.status == "connected":
                    logger.info(
                        "MCP reconnect '%s' succeeded on attempt %d", name, attempt + 1
                    )
                    return result
                # Connection returned but not connected — raise to trigger retry
                raise MCPError(
                    code=-32000,
                    message=result.error or f"Reconnect '{name}' returned status {result.status}",
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= self._reconnection_config.max_retries:
                    break
                delay = _backoff_delay(attempt, self._reconnection_config)
                logger.warning(
                    "MCP reconnect '%s' failed (attempt %d/%d): %s. Retrying in %.1fs",
                    name,
                    attempt + 1,
                    self._reconnection_config.max_retries + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        logger.error(
            "MCP reconnect '%s' exhausted all retries: %s", name, last_exc
        )
        return MCPServerConnection(
            name=name,
            config=config,
            status="failed",
            connected=False,
            error=str(last_exc) if last_exc else "Reconnection exhausted",
        )

    async def health_check(self, name: str) -> bool:
        """Check if a server is still responsive by sending a ping.

        Uses 'ping' method if the server advertises it in capabilities,
        otherwise falls back to listing tools as a lightweight check.
        """
        session = self.sessions.get(name)
        if session is None or session._closed:
            return False

        try:
            # Try ping if the server supports it
            caps = session.capabilities if hasattr(session, "capabilities") else {}
            if caps.get("experimental", {}).get("ping"):
                await self._send_request_safe(
                    session, "ping", {}, timeout=10.0, server_name=name
                )
                return True

            # Fallback: lightweight tool list (fast, low overhead)
            await self._send_request_safe(
                session, "tools/list", {}, timeout=10.0, server_name=name
            )
            return True
        except Exception:
            return False

    async def start_health_checks(self, name: str, config: McpServerConfig) -> None:
        """Start periodic health checks for a server. Auto-reconnects on failure."""
        # Cancel any existing health task
        await self.stop_health_checks(name)

        async def _health_loop() -> None:
            interval = self._reconnection_config.health_check_interval
            while True:
                await asyncio.sleep(interval)
                session = self.sessions.get(name)
                if session is None or session._closed:
                    break
                healthy = await self.health_check(name)
                if not healthy:
                    logger.warning(
                        "MCP health check failed for '%s', triggering reconnect", name
                    )
                    await self.reconnect_with_backoff(name, config)

        self._health_tasks[name] = asyncio.ensure_future(_health_loop())
        logger.debug("MCP health checks started for '%s'", name)

    async def stop_health_checks(self, name: str) -> None:
        """Stop periodic health checks for a server."""
        task = self._health_tasks.pop(name, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def connect_stdio(
        self, name: str, command: list[str], **kwargs: Any
    ) -> MCPServerConnection:
        """Connect via stdio transport."""
        config = McpStdioServerConfig(
            command=command[0] if command else "",
            args=command[1:] if len(command) > 1 else [],
            env=kwargs.get("env", {}),
        )
        return await self.connect(name, config)

    async def connect_remote(
        self, name: str, url: str, **kwargs: Any
    ) -> MCPServerConnection:
        """Connect via SSE/HTTP transport."""
        config = McpSseServerConfig(
            url=url,
            headers=kwargs.get("headers", {}),
        )
        return await self.connect(name, config)

    # ------------------------------------------------------------------
    # Transport setup
    # ------------------------------------------------------------------

    async def _connect_transport(
        self, name: str, config: McpServerConfig
    ) -> StdioSession | RemoteSession:
        transport = getattr(config, "type", "stdio")
        if transport == "stdio":
            return await self._connect_stdio(name, config)
        if transport in ("sse", "http", "ws"):
            return await self._connect_remote(name, config)
        raise ValueError(f"Unsupported MCP transport: {transport}")

    async def _connect_stdio(self, name: str, config: McpServerConfig) -> StdioSession:
        if not isinstance(config, McpStdioServerConfig):
            raise TypeError("Expected McpStdioServerConfig for stdio transport")

        cmd = config.command
        args = list(config.args) if config.args else []
        env = {**os.environ, **(config.env or {})}

        proc = subprocess.Popen(
            [cmd, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        session = StdioSession(server_name=name, process=proc)

        # Start reader loop
        loop = asyncio.get_running_loop()
        session.reader_task = loop.create_task(
            self._read_loop(session), name=f"mcp-reader-{name}"
        )

        # Initialize handshake
        try:
            result = await self._send_request(
                session,
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "hare",
                        "version": "2.1.88",
                    },
                },
                timeout=INIT_TIMEOUT,
            )
            session.capabilities = result.get("capabilities", {})
            session.server_info = result.get("serverInfo", {})

            # Send initialized notification
            await self._send_notification(session, "notifications/initialized", {})
        except Exception:
            await self._close_session(session)
            raise

        return session

    async def _connect_remote(
        self, name: str, config: McpServerConfig
    ) -> RemoteSession:
        """Connect to a remote MCP server via SSE transport.

        Uses the SSEClientTransport for a full bidirectional JSON-RPC session
        over SSE (long-lived GET for events, POST for requests).
        Implements the @modelcontextprotocol/sdk SSE transport flow:
        connect → receive 'endpoint' event → send initialize → tools/list.
        """
        transport_type = getattr(config, "type", "sse")
        url = getattr(config, "url", "")
        if not url:
            raise ValueError(f"Missing url for {transport_type} transport '{name}'")

        headers = dict(getattr(config, "headers", {}) or {})
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json, text/event-stream")

        # Resolve headers_helper if present (e.g. apiKeyHelper script)
        headers_helper = getattr(config, "headers_helper", None)
        if headers_helper:
            try:
                proc = subprocess.run(
                    headers_helper,
                    shell=True,  # nosec B602
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    for line in proc.stdout.strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            headers[k.strip()] = v.strip()
            except Exception:
                pass

        sse_transport = SSEClientTransport(
            url=url,
            headers=headers,
            reconnection=self._reconnection_config,
        )

        # Open the SSE stream and discover the POST endpoint
        try:
            await sse_transport.connect()
        except MCPError:
            raise
        except Exception as exc:
            raise McpError(
                code=-32000,
                message=f"SSE connection to {url} failed: {exc}",
            ) from exc

        # JSON-RPC initialize handshake over SSE
        try:
            result = await sse_transport.send_request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "hare", "version": "2.1.88"},
                },
                timeout=INIT_TIMEOUT,
            )
        except MCPError:
            await sse_transport.close()
            raise
        except Exception as exc:
            await sse_transport.close()
            raise McpError(
                code=-32000,
                message=f"MCP initialize failed for {name}: {exc}",
            ) from exc

        session = RemoteSession(
            server_name=name,
            transport=sse_transport,
            capabilities=result.get("capabilities", {}),
            server_info=result.get("serverInfo", {}),
        )

        # Send initialized notification
        try:
            await sse_transport.send_notification("notifications/initialized", {})
        except Exception:
            pass

        return session

    # ------------------------------------------------------------------
    # JSON-RPC I/O
    # ------------------------------------------------------------------

    async def _read_loop(self, session: StdioSession) -> None:
        """Read JSON-RPC messages from the subprocess stdout."""
        loop = asyncio.get_running_loop()
        buffer = b""

        try:
            while not session._closed:
                try:
                    chunk = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            session.process.stdout.read,  # type: ignore[union-attr]
                            4096,
                        ),
                        timeout=0.5,
                    )
                except asyncio.TimeoutError:
                    continue

                if not chunk:
                    break  # EOF — process exited

                buffer += chunk

                # Process complete lines
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    self._handle_message(session, msg)
        except Exception:
            pass

    def _handle_message(self, session: StdioSession, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        if "error" in msg or "result" in msg:
            if msg_id is not None and msg_id in session.pending:
                pending = session.pending.pop(msg_id)
                if "error" in msg:
                    pending.future.set_exception(
                        MCPError(
                            code=msg["error"].get("code", -1),
                            message=msg["error"].get("message", "Unknown MCP error"),
                        )
                    )
                else:
                    pending.future.set_result(msg["result"])

    async def _send_request(
        self,
        session: StdioSession | RemoteSession,
        method: str,
        params: dict[str, Any],
        timeout: float = TOOL_CALL_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request. Dispatches to the appropriate transport."""
        if isinstance(session, RemoteSession):
            return await session.transport.send_request(method, params, timeout=timeout)

        # StdioSession path (unchanged)
        async with session._lock:
            req_id = session.next_id()
            payload = json.dumps(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": req_id,
                    "method": method,
                    "params": params,
                }
            )
            future: asyncio.Future[dict[str, Any]] = asyncio.Future()
            session.pending[req_id] = _PendingRequest(future=future, method=method)

            try:
                session.process.stdin.write(payload.encode("utf-8") + b"\n")  # type: ignore[union-attr]
                session.process.stdin.flush()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError) as exc:
                session.pending.pop(req_id, None)
                raise MCPError(code=-32000, message=f"Write failed: {exc}")

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            session.pending.pop(req_id, None)
            raise MCPError(code=-32000, message=f"Request timed out: {method}")

    async def _send_request_safe(
        self,
        session: StdioSession | RemoteSession,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 10.0,
        server_name: str = "",
    ) -> dict[str, Any] | None:
        """Send a request and return None on failure instead of raising.

        Used by health checks and optional operations where failure is not fatal.
        """
        try:
            return await self._send_request(session, method, params, timeout=timeout)
        except MCPError:
            return None
        except Exception:
            if server_name:
                logger.debug("MCP safe request '%s' failed for '%s'", method, server_name, exc_info=True)
            return None

    async def _send_notification(
        self, session: StdioSession | RemoteSession, method: str, params: dict[str, Any]
    ) -> None:
        """Send a JSON-RPC notification. Dispatches to the appropriate transport."""
        if isinstance(session, RemoteSession):
            await session.transport.send_notification(method, params)
            return

        # StdioSession path (unchanged)
        async with session._lock:
            payload = json.dumps(
                {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}
            )
            try:
                session.process.stdin.write(payload.encode("utf-8") + b"\n")  # type: ignore[union-attr]
                session.process.stdin.flush()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass

    async def _close_session(self, session: StdioSession | RemoteSession) -> None:
        """Close a session, cleaning up transport resources."""
        session._closed = True

        if isinstance(session, RemoteSession):
            await session.transport.close()
            return

        # StdioSession path (unchanged)
        if session.reader_task is not None:
            session.reader_task.cancel()
            try:
                await session.reader_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            session.process.terminate()
            try:
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.process.kill()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Tool listing
    # ------------------------------------------------------------------

    async def list_tools(self, server_name: str) -> list[dict[str, Any]]:
        """List tools available on a connected MCP server (LRU-memoized).

        Matching TS fetchToolsForClient with LRU memoization by server name.
        """
        # Check cache first
        cached = self._tools_cache.get(server_name)
        if cached is not None:
            return cached

        session = self.sessions.get(server_name)
        if session is None or session._closed:
            raise MCPError(code=-32000, message=f"Not connected to {server_name}")

        try:
            result = await self._send_request(session, "tools/list", {}, timeout=30.0)
        except MCPError:
            return []

        tools = result.get("tools", [])
        out: list[dict[str, Any]] = []
        for t in tools:
            out.append(
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema", t.get("input_schema", {})),
                    "annotations": t.get("annotations", {}),
                }
            )
        self._tools_cache.set(server_name, out)
        return out

    async def list_commands(self, server_name: str) -> list[dict[str, Any]]:
        """List prompts/commands on a connected MCP server (LRU-memoized).

        Matching TS fetchCommandsForClient.
        """
        cached = self._commands_cache.get(server_name)
        if cached is not None:
            return cached

        session = self.sessions.get(server_name)
        if session is None or session._closed:
            return []

        try:
            result = await self._send_request(session, "prompts/list", {}, timeout=30.0)
        except MCPError:
            return []

        commands = result.get("prompts", [])
        self._commands_cache.set(server_name, commands)
        return commands

    # ------------------------------------------------------------------
    # Tool calling
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call a tool on a connected MCP server."""
        session = self.sessions.get(server_name)
        if session is None or session._closed:
            return {
                "content": [],
                "is_error": True,
                "error": f"Not connected to MCP server: {server_name}",
            }

        try:
            result = await self._send_request(
                session,
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
                timeout=TOOL_CALL_TIMEOUT,
            )
            return {
                "content": result.get("content", []),
                "is_error": result.get("isError", False),
                "_meta": result.get("_meta", {}),
                "structuredContent": result.get("structuredContent"),
            }
        except MCPError as exc:
            return {
                "content": [{"type": "text", "text": f"MCP error: {exc.message}"}],
                "is_error": True,
                "error": exc.message,
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": str(exc)}],
                "is_error": True,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Resource listing
    # ------------------------------------------------------------------

    async def list_resources(self, server_name: str) -> list[dict[str, Any]]:
        """List resources available on a connected MCP server."""
        session = self.sessions.get(server_name)
        if session is None or session._closed:
            return []
        try:
            result = await self._send_request(
                session, "resources/list", {}, timeout=30.0
            )
        except MCPError:
            return []
        return result.get("resources", [])

    def list_servers(self) -> list[str]:
        """Return the names of all connected MCP servers."""
        return list(self.sessions.keys())

    async def read_resource(self, server_name: str, uri: str) -> dict[str, Any]:
        """Read a specific resource from a connected MCP server.
        Issues a resources/read JSON-RPC request matching the MCP spec."""
        session = self.sessions.get(server_name)
        if session is None or session._closed:
            raise MCPError(code=-32000, message=f"Not connected to MCP server: {server_name}")
        result = await self._send_request(
            session, "resources/read", {"uri": uri}, timeout=30.0
        )
        return {"data": result.get("contents", [])}


    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    async def disconnect(self, name: str) -> None:
        """Disconnect and clean up an MCP server connection."""
        async with self._lock:
            session = self.sessions.pop(name, None)
            self.connection_results.pop(name, None)
        if session is not None:
            await self._close_session(session)

    async def disconnect_all(self) -> None:
        """Disconnect all MCP server connections."""
        # Stop all health checks
        health_names = list(self._health_tasks.keys())
        for name in health_names:
            await self.stop_health_checks(name)

        async with self._lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
            self.connection_results.clear()
            self._tools_cache.clear()
            self._commands_cache.clear()
            self._resources_cache.clear()
        for session in sessions:
            await self._close_session(session)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_connected(
        self,
        name: str,
        config: McpServerConfig,
        session: StdioSession | RemoteSession,
        status: ConnectionStatus = "connected",
    ) -> MCPServerConnection:
        return MCPServerConnection(
            name=name,
            config=config,
            status=status,
            connected=(status == "connected"),
            capabilities=session.capabilities,
            server_info=session.server_info,
            tools=[],
        )

    def is_connected(self, name: str) -> bool:
        session = self.sessions.get(name)
        return session is not None and not session._closed

    def get_connection(self, name: str) -> Optional[MCPServerConnection]:
        return self.connection_results.get(name)


# ---------------------------------------------------------------------------
# Tool wrapping (matching TS fetchToolsForClient tool construction)
# ---------------------------------------------------------------------------


def _wrap_tool(server_name: str, raw: dict[str, Any]) -> McpToolInfo:
    """Wrap a raw MCP tool dict into a McpToolInfo with annotations.

    Matching TS fetchToolsForClient lines 1777-1842.
    """
    annotations = raw.get("annotations", {})
    input_schema = raw.get("inputSchema", raw.get("input_schema", {}))
    return McpToolInfo(
        name=f"mcp__{server_name}__{raw.get('name', '')}",
        description=raw.get("description", "")[:2048],  # MAX_MCP_DESCRIPTION_LENGTH
        input_schema=input_schema,
        server_name=server_name,
        annotations={
            "readOnlyHint": annotations.get("readOnlyHint", False),
            "destructiveHint": annotations.get("destructiveHint", False),
            "openWorldHint": annotations.get("openWorldHint", False),
        },
        is_mcp=True,
    )


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class MCPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"MCP error [{code}]: {message}")
        self.code = code
        self.message = message


McpError = MCPError  # alias so bare raise McpError(...) resolves at runtime


# ---------------------------------------------------------------------------
# Singleton pool
# ---------------------------------------------------------------------------

_default_pool: Optional[McpClientPool] = None


def get_mcp_client_pool() -> McpClientPool:
    global _default_pool
    if _default_pool is None:
        _default_pool = McpClientPool()
    return _default_pool


def reset_mcp_client_pool() -> None:
    global _default_pool
    _default_pool = None
