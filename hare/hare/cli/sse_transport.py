"""
SSE transport — SSE reads + POST writes for CCR v2.

Port of: src/cli/transports/SSETransport.ts
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Any, Callable

DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 300.0


def _has_aiohttp() -> bool:
    try:
        import aiohttp  # noqa: F401
        return True
    except ImportError:
        return False


def _has_httpx() -> bool:
    try:
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


class SSETransport:
    def __init__(
        self, url: str, headers: dict[str, str] | None = None,
        session_id: str | None = None,
        refresh_headers: Callable[[], dict[str, str]] | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._session_id = session_id
        self._refresh_headers = refresh_headers
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._connected = False
        self._closed = False
        self._on_data: Callable[[str], None] | None = None
        self._on_close: Callable[..., None] | None = None
        self._on_event: Callable[..., None] | None = None
        self._last_event_id: str | None = None
        self._last_data_time: float = 0.0
        self._sse_task: asyncio.Task[Any] | None = None
        self._liveness_task: asyncio.Task[Any] | None = None

    def set_on_data(self, cb: Callable[[str], None]) -> None:
        self._on_data = cb

    def set_on_close(self, cb: Callable[..., None]) -> None:
        self._on_close = cb

    def set_on_event(self, cb: Callable[..., None]) -> None:
        self._on_event = cb

    async def connect(self) -> None:
        self._closed = False
        backoff_ms = 1000
        max_backoff = 30_000
        start_time = time.time()
        give_up_ms = 600_000

        while not self._closed:
            elapsed = (time.time() - start_time) * 1000
            if elapsed > give_up_ms:
                if self._on_close:
                    self._on_close()
                return
            try:
                self._connected = True
                self._last_data_time = time.time()
                self._sse_task = asyncio.ensure_future(self._read_sse_stream())
                self._liveness_task = asyncio.ensure_future(self._liveness_check())
                return
            except Exception:
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms = min(backoff_ms * 2, max_backoff)
                if self._refresh_headers:
                    self._headers = self._refresh_headers()

    async def _read_sse_stream(self) -> None:
        if _has_aiohttp():
            await self._read_sse_aiohttp()
        elif _has_httpx():
            await self._read_sse_httpx()
        else:
            await self._read_sse_urllib()

    async def _read_sse_aiohttp(self) -> None:
        import aiohttp
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache", **self._headers}
        if self._session_id:
            headers["X-Session-Id"] = self._session_id
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id
        timeout = aiohttp.ClientTimeout(connect=self._connect_timeout, sock_read=self._read_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self._url, headers=headers) as response:
                    response.raise_for_status()
                    event_type = ""; data_buffer = ""; last_id: str | None = None
                    async for line_bytes in response.content:
                        if not self._connected or self._closed:
                            break
                        self._last_data_time = time.time()
                        try:
                            line = line_bytes.decode("utf-8").rstrip("\r\n")
                        except UnicodeDecodeError:
                            continue
                        if not line:
                            if data_buffer:
                                self._dispatch_event(event_type or "message", data_buffer, last_id)
                            event_type = ""; data_buffer = ""
                            continue
                        if line.startswith(":"):
                            continue
                        elif line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("id:"):
                            last_id = line[3:].strip(); self._last_event_id = last_id
                        elif line.startswith("data:"):
                            data = line[5:]
                            if data.startswith(" "):
                                data = data[1:]
                            data_buffer += data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        finally:
            if not self._closed:
                asyncio.ensure_future(self.connect())

    async def _read_sse_httpx(self) -> None:
        import httpx
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache", **self._headers}
        if self._session_id:
            headers["X-Session-Id"] = self._session_id
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=self._connect_timeout, read=self._read_timeout)) as client:
                async with client.stream("GET", self._url, headers=headers) as response:
                    response.raise_for_status()
                    event_type = ""; data_buffer = ""; last_id: str | None = None
                    async for line in response.aiter_lines():
                        if not self._connected or self._closed:
                            break
                        self._last_data_time = time.time()
                        if not line:
                            if data_buffer:
                                self._dispatch_event(event_type or "message", data_buffer, last_id)
                            event_type = ""; data_buffer = ""
                            continue
                        if line.startswith(":"):
                            continue
                        elif line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("id:"):
                            last_id = line[3:].strip(); self._last_event_id = last_id
                        elif line.startswith("data:"):
                            data = line[5:]
                            if data.startswith(" "):
                                data = data[1:]
                            data_buffer += data
        except (httpx.HTTPError, asyncio.TimeoutError, OSError):
            pass
        finally:
            if not self._closed:
                asyncio.ensure_future(self.connect())

    async def _read_sse_urllib(self) -> None:
        def _sync_read() -> list[tuple[str, str, str | None]]:
            events: list[tuple[str, str, str | None]] = []
            req = urllib.request.Request(self._url)
            req.add_header("Accept", "text/event-stream")
            req.add_header("Cache-Control", "no-cache")
            for k, v in self._headers.items():
                req.add_header(k, v)
            if self._last_event_id:
                req.add_header("Last-Event-ID", self._last_event_id)
            try:
                with urllib.request.urlopen(req, timeout=self._read_timeout) as resp:
                    event_type = ""; data_buffer = ""; last_id: str | None = None
                    for raw_line in resp:
                        try:
                            line = raw_line.decode("utf-8").rstrip("\r\n") if isinstance(raw_line, bytes) else raw_line.rstrip("\r\n")
                        except UnicodeDecodeError:
                            continue
                        if not line:
                            if data_buffer:
                                events.append((event_type or "message", data_buffer, last_id))
                            event_type = ""; data_buffer = ""
                            continue
                        if line.startswith(":"):
                            continue
                        elif line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("id:"):
                            last_id = line[3:].strip()
                        elif line.startswith("data:"):
                            data = line[5:]
                            if data.startswith(" "):
                                data = data[1:]
                            data_buffer += data
            except (OSError, TimeoutError, ValueError):
                pass
            return events

        while self._connected and not self._closed:
            try:
                events = await asyncio.get_event_loop().run_in_executor(None, _sync_read)
                for ev_type, data, ev_id in events:
                    self._last_data_time = time.time()
                    if ev_id:
                        self._last_event_id = ev_id
                    self._dispatch_event(ev_type, data, ev_id)
                break
            except Exception:
                break
        if not self._closed:
            asyncio.ensure_future(self.connect())

    def _dispatch_event(self, event_type: str, data: str, event_id: str | None) -> None:
        if self._on_event:
            try:
                self._on_event(event_type, data, event_id)
            except Exception:
                pass
        if self._on_data:
            try:
                self._on_data(data)
            except Exception:
                pass

    async def _liveness_check(self) -> None:
        while self._connected:
            await asyncio.sleep(45)
            if time.time() - self._last_data_time > 45:
                self._connected = False
                if self._sse_task and not self._sse_task.done():
                    self._sse_task.cancel()
                asyncio.ensure_future(self.connect())
                return

    async def write(self, message: Any) -> None:
        body = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        post_url = self._url.replace("/stream", "")
        headers = {"Content-Type": "application/json", **self._headers}
        if self._session_id:
            headers["X-Session-Id"] = self._session_id
        last_error: Exception | None = None
        for attempt in range(10):
            try:
                if _has_aiohttp():
                    import aiohttp
                    timeout = aiohttp.ClientTimeout(connect=self._connect_timeout, total=self._connect_timeout + 30)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(post_url, data=body, headers=headers) as resp:
                            if resp.status < 400:
                                return
                            last_error = RuntimeError(f"POST returned {resp.status}")
                else:
                    def _sync_post() -> None:
                        req = urllib.request.Request(post_url, data=body.encode("utf-8"), headers=headers, method="POST")
                        with urllib.request.urlopen(req, timeout=self._connect_timeout + 30):
                            pass
                    await asyncio.get_event_loop().run_in_executor(None, _sync_post)
                    return
            except Exception as e:
                last_error = e
            if attempt < 9:
                delay = min(1000 * (2**attempt), 30_000) / 1000
                await asyncio.sleep(delay)
                if self._refresh_headers:
                    self._headers = self._refresh_headers()
                    headers = {"Content-Type": "application/json", **self._headers}
                    if self._session_id:
                        headers["X-Session-Id"] = self._session_id
            else:
                if last_error:
                    raise last_error
                raise RuntimeError("POST failed after 10 attempts")

    def close(self) -> None:
        self._closed = True
        self._connected = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
        if self._liveness_task and not self._liveness_task.done():
            self._liveness_task.cancel()
