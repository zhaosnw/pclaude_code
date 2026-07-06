"""
CCR v2 client — full worker lifecycle: heartbeat, event uploaders, state uploader,
delivery tracking, epoch management, paginated GETs, auth failure handling.

Port of: src/cli/transports/ccrClient.ts
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any, Callable, Literal, Optional

from hare.cli.serial_batch_uploader import SerialBatchEventUploader
from hare.cli.worker_state_uploader import WorkerStateUploader

CCRInitFailReason = Literal[
    "no_auth_headers", "missing_epoch", "worker_register_failed"
]


class CCRInitError(Exception):
    def __init__(self, reason: CCRInitFailReason) -> None:
        super().__init__(f"CCRClient init failed: {reason}")
        self.reason = reason


class CCRClient:
    """Full CCR v2 worker lifecycle client."""

    def __init__(
        self,
        session_url: str,
        headers: Optional[dict[str, str]] = None,
        session_id: Optional[str] = None,
        http_post: Any = None,
        http_get: Any = None,
        on_auth_failure: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._session_url = session_url.rstrip("/")
        self._headers = headers or {}
        self._session_id = session_id
        self._http_post = http_post
        self._http_get = http_get
        self._on_auth_failure = on_auth_failure

        self._epoch: Optional[int] = None
        self._jwt_expiry: Optional[float] = None
        self._auth_failures = 0
        self._max_auth_failures = 3
        self._closed = False

        # Heartbeat
        self._heartbeat_task: Optional[asyncio.Task[Any]] = None

        # Three uploaders matching TS
        self._event_uploader = SerialBatchEventUploader[Any](
            max_batch_size=500,
            max_queue_size=100_000,
            send=self._send_events,
            base_delay_ms=1000,
            max_delay_ms=30_000,
            jitter_ms=500,
        )
        self._internal_event_uploader = SerialBatchEventUploader[Any](
            max_batch_size=100,
            max_queue_size=10_000,
            send=self._send_internal_events,
            base_delay_ms=1000,
            max_delay_ms=30_000,
            jitter_ms=500,
        )
        self._worker_state_uploader = WorkerStateUploader(
            send=self._send_worker_state,
            base_delay_ms=1000,
            max_delay_ms=30_000,
            jitter_ms=500,
        )

        # Stream event delay buffer
        self._stream_buffer: list[Any] = []
        self._stream_flush_task: Optional[asyncio.Task[Any]] = None

        # Delivery tracking
        self._delivery_uploader = SerialBatchEventUploader[Any](
            max_batch_size=100,
            max_queue_size=10_000,
            send=self._send_delivery,
            base_delay_ms=1000,
            max_delay_ms=10_000,
            jitter_ms=200,
        )

    async def initialize(self, epoch: Optional[int] = None) -> Optional[dict[str, Any]]:
        """Register as worker, start heartbeat, return metadata."""
        if epoch is not None:
            self._epoch = epoch
        elif os.environ.get("CLAUDE_CODE_WORKER_EPOCH"):
            try:
                self._epoch = int(os.environ["CLAUDE_CODE_WORKER_EPOCH"])
            except ValueError:
                raise CCRInitError("missing_epoch")
        else:
            raise CCRInitError("missing_epoch")

        if not self._http_post:
            raise CCRInitError("worker_register_failed")

        try:
            resp = await self._http_post(
                f"{self._session_url}/worker/register",
                json={},
                headers=self._headers,
                timeout=10,
            )
            if resp.get("status") not in (200, 201):
                raise CCRInitError("worker_register_failed")
        except CCRInitError:
            raise
        except Exception:
            raise CCRInitError("worker_register_failed")

        # Start heartbeat
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

        return {"worker_epoch": self._epoch, "session_url": self._session_url}

    async def _heartbeat_loop(self) -> None:
        """Periodic heartbeat with jitter."""
        while not self._closed:
            try:
                await asyncio.sleep(30 + random.uniform(0, 5))
                await self._send_heartbeat()
            except Exception:
                self._auth_failures += 1
                if self._auth_failures >= self._max_auth_failures:
                    if self._on_auth_failure:
                        self._on_auth_failure()
                    return

    async def _send_heartbeat(self) -> None:
        if not self._http_post:
            return
        await self._http_post(
            f"{self._session_url}/worker/heartbeat",
            json={"worker_epoch": self._epoch},
            headers=self._headers,
            timeout=10,
        )
        self._auth_failures = 0

    # ---- Event writing ----

    async def write_event(self, message: Any) -> None:
        """Route stream events through delay buffer, others through uploader."""
        is_stream = isinstance(message, dict) and message.get("type") == "stream_event"
        if is_stream:
            self._stream_buffer.append(message)
            if not self._stream_flush_task:
                self._stream_flush_task = asyncio.ensure_future(self._flush_stream())
        else:
            if self._stream_buffer:
                await self._flush_now()
            await self._event_uploader.enqueue(message)

    async def write_internal_event(
        self, event_type: str, payload: dict[str, Any], **opts: Any
    ) -> None:
        await self._internal_event_uploader.enqueue(
            {
                "type": event_type,
                **payload,
                "timestamp": int(time.time() * 1000),
            }
        )

    async def flush_internal_events(self) -> None:
        await self._internal_event_uploader.flush()

    @property
    def internal_events_pending(self) -> int:
        return self._internal_event_uploader.pending_count

    async def _flush_stream(self) -> None:
        await asyncio.sleep(0.1)
        await self._flush_now()

    async def _flush_now(self) -> None:
        if not self._stream_buffer:
            return
        batch = list(self._stream_buffer)
        self._stream_buffer.clear()
        self._stream_flush_task = None
        await self._event_uploader.enqueue(batch)

    # ---- Report delivery ----

    def report_delivery(self, seq: int) -> None:
        asyncio.ensure_future(
            self._delivery_uploader.enqueue({"last_sequence_num": seq})
        )

    async def _send_events(self, batch: list[Any]) -> None:
        if self._http_post:
            await self._http_post(
                f"{self._session_url}/worker/events",
                json={"events": batch},
                headers=self._headers,
                timeout=30,
            )

    async def _send_internal_events(self, batch: list[Any]) -> None:
        if self._http_post:
            await self._http_post(
                f"{self._session_url}/worker/internal_events",
                json={"events": batch},
                headers=self._headers,
                timeout=30,
            )

    async def _send_worker_state(self, payload: dict[str, Any]) -> None:
        if self._http_post:
            await self._http_post(
                f"{self._session_url}/worker/state",
                json=payload,
                headers=self._headers,
                timeout=10,
            )

    async def _send_delivery(self, batch: list[Any]) -> None:
        if self._http_post:
            await self._http_post(
                f"{self._session_url}/worker/delivery",
                json={"delivery": batch},
                headers=self._headers,
                timeout=10,
            )

    # ---- Paginated GET ----

    async def read_internal_events(
        self, limit: int = 100, before_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        if not self._http_get:
            return None
        params: dict[str, Any] = {"limit": limit}
        if before_id:
            params["before_id"] = before_id
        resp = await self._http_get(
            f"{self._session_url}/worker/internal_events",
            params=params,
            headers=self._headers,
            timeout=15,
        )
        if resp.get("status") == 200:
            return resp.get("data", {}) if isinstance(resp.get("data"), dict) else {}
        return None

    # ---- State updates ----

    def update_state(self, patch: dict[str, Any]) -> None:
        self._worker_state_uploader.enqueue(patch)

    def close(self) -> None:
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        self._event_uploader.close()
        self._internal_event_uploader.close()
        self._worker_state_uploader.close()
        self._delivery_uploader.close()
