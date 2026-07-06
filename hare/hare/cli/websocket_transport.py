"""
WebSocket transport with auto-reconnect, ping/keepalive, and message buffer.

Port of: src/cli/transports/WebSocketTransport.ts
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

DEFAULT_PING_INTERVAL = 10_000  # ms
DEFAULT_KEEPALIVE_INTERVAL = 300_000  # 5 min


class WebSocketTransport:
    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 session_id: str | None = None,
                 refresh_headers: Callable[[], dict[str, str]] | None = None) -> None:
        self._url = url
        self._headers = headers or {}
        self._session_id = session_id
        self._refresh_headers = refresh_headers
        self._ws: Any = None
        self._connected = False
        self._on_data: Callable[[str], None] | None = None
        self._on_close: Callable[..., None] | None = None
        self._on_connect: Callable[..., None] | None = None
        self._message_buffer: list[Any] = []
        self._ping_task: asyncio.Task[Any] | None = None
        self._receive_task: asyncio.Task[Any] | None = None
        self._reconnect_task: asyncio.Task[Any] | None = None
        self._closed = False
        self._last_sent_id: int = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_on_data(self, cb: Callable[[str], None]) -> None:
        self._on_data = cb

    def set_on_close(self, cb: Callable[..., None]) -> None:
        self._on_close = cb

    def set_on_connect(self, cb: Callable[..., None]) -> None:
        self._on_connect = cb

    async def connect(self) -> None:
        self._closed = False
        backoff_ms = 1000; max_backoff = 30_000
        start_time = time.time(); give_up_ms = 600_000
        while not self._closed:
            elapsed = (time.time() - start_time) * 1000
            if elapsed > give_up_ms:
                if self._on_close:
                    self._on_close()
                return
            try:
                await self._do_connect()
                self._connected = True
                if self._on_connect:
                    self._on_connect()
                for msg in self._message_buffer:
                    await self._write_raw(msg)
                self._message_buffer.clear()
                self._ping_task = asyncio.ensure_future(self._ping_loop())
                self._receive_task = asyncio.ensure_future(self._receive_loop())
                return
            except Exception:
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms = min(backoff_ms * 2, max_backoff)
                if self._refresh_headers:
                    self._headers = self._refresh_headers()

    async def _do_connect(self) -> None:
        try:
            import websockets
        except ImportError:
            raise ImportError("WebSocket transport requires 'websockets'. Install: pip install websockets") from None
        extra_headers = [(k, v) for k, v in self._headers.items()]
        if self._session_id:
            extra_headers.append(("X-Session-Id", self._session_id))
        self._ws = await websockets.connect(self._url, extra_headers=extra_headers,
                                             ping_interval=DEFAULT_KEEPALIVE_INTERVAL / 1000, close_timeout=5)

    async def _receive_loop(self) -> None:
        while self._connected and self._ws is not None:
            try:
                message = await self._ws.recv()
            except Exception:
                break
            if isinstance(message, bytes):
                try:
                    message = message.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            if self._on_data and isinstance(message, str):
                try:
                    self._on_data(message)
                except Exception:
                    pass
        if self._connected and not self._closed:
            self._connected = False
            asyncio.ensure_future(self._reconnect())

    async def _ping_loop(self) -> None:
        while self._connected:
            try:
                await self._send_ping()
            except Exception:
                self._connected = False
                if not self._closed:
                    asyncio.ensure_future(self._reconnect())
                return
            await asyncio.sleep(DEFAULT_PING_INTERVAL / 1000)

    async def _send_ping(self) -> None:
        if self._ws is not None:
            try:
                pong = await self._ws.ping()
                await asyncio.wait_for(pong, timeout=10)
            except Exception:
                raise

    async def write(self, message: Any) -> None:
        if self._connected:
            try:
                await self._write_raw(message)
            except Exception:
                self._message_buffer.append(message)
                self._connected = False
                asyncio.ensure_future(self._reconnect())
        else:
            self._message_buffer.append(message)

    async def _write_raw(self, message: Any) -> None:
        data = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        self._last_sent_id += 1
        if self._ws is not None:
            await self._ws.send(data)

    async def _reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.ensure_future(self.connect())

    def close(self) -> None:
        self._closed = True
        self._connected = False
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self._ws is not None:
            try:
                asyncio.ensure_future(self._ws.close())
            except Exception:
                pass
            self._ws = None
        self._message_buffer.clear()
