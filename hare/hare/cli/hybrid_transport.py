"""
Hybrid transport — WebSocket reads + HTTP POST writes.

Port of: src/cli/transports/HybridTransport.ts

Extends WebSocketTransport with POST-based write path:
- stream_event: buffered with 100ms delay, flushed in batch
- non-stream_event: flushes buffer then enqueues to SerialBatchEventUploader
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional

from hare.cli.websocket_transport import WebSocketTransport
from hare.cli.serial_batch_uploader import SerialBatchEventUploader

CLOSE_GRACE_MS = 5000


class HybridTransport(WebSocketTransport):
    def __init__(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        session_id: Optional[str] = None,
        refresh_headers: Optional[Callable[[], dict[str, str]]] = None,
    ) -> None:
        super().__init__(url, headers, session_id, refresh_headers)

        # POST-based uploader for non-stream events
        self._uploader = SerialBatchEventUploader[Any](
            max_batch_size=500,
            max_queue_size=100_000,
            send=self._post_batch,
            base_delay_ms=1000,
            max_delay_ms=30_000,
            jitter_ms=500,
        )

        # Stream event delay buffer (100ms)
        self._stream_buffer: list[Any] = []
        self._stream_flush_task: Optional[asyncio.Task[Any]] = None

        # POST URL derived from WS URL
        self._post_url = self._convert_ws_url_to_post_url(url)

    @staticmethod
    def _convert_ws_url_to_post_url(ws_url: str) -> str:
        """Convert ws:// URL to https:// POST URL."""
        url = ws_url.replace("ws://", "http://").replace("wss://", "https://")
        return url.replace("/ws/", "/session/")

    async def write(self, message: Any) -> None:
        """Route stream events through buffer, others through uploader."""
        is_stream = isinstance(message, dict) and message.get("type") == "stream_event"
        if is_stream:
            self._stream_buffer.append(message)
            if not self._stream_flush_task:
                self._stream_flush_task = asyncio.ensure_future(
                    self._flush_stream_buffer()
                )
        else:
            # Flush stream buffer first so events stay ordered
            if self._stream_buffer:
                await self._flush_now()
            await self._uploader.enqueue(message)

    async def write_batch(self, messages: list[Any]) -> None:
        for msg in messages:
            await self.write(msg)

    async def _flush_stream_buffer(self) -> None:
        """Flush stream buffer after 100ms delay."""
        await asyncio.sleep(0.1)
        await self._flush_now()

    async def _flush_now(self) -> None:
        if not self._stream_buffer:
            return
        batch = list(self._stream_buffer)
        self._stream_buffer.clear()
        self._stream_flush_task = None
        await self._post_batch(batch)

    async def _post_batch(self, batch: list[Any]) -> None:
        """POST a batch of events to the session endpoint."""
        import urllib.request

        body = "\n".join(
            msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
            for msg in batch
        )
        req = urllib.request.Request(
            self._post_url,
            data=body.encode("utf-8"),
            headers={**self._headers, "Content-Type": "application/x-ndjson"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"POST failed: {resp.status}")
        except Exception:
            raise

    async def close(self) -> None:
        """Graceful close: flush pending, close uploader, then super.close()."""
        if self._stream_buffer:
            await self._flush_now()
        try:
            await asyncio.wait_for(self._uploader.flush(), CLOSE_GRACE_MS / 1000)
        except asyncio.TimeoutError:
            pass
        self._uploader.close()
        await super().close()
