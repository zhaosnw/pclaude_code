"""
Serial batch event uploader with batching, retry, and backpressure.

Port of: src/cli/transports/SerialBatchEventUploader.ts

- enqueue() adds events; blocks when maxQueueSize reached
- At most 1 POST in-flight at a time
- Drains up to maxBatchSize items per POST (optional maxBatchBytes)
- On failure: exponential backoff, retries until success or close()
- flush() blocks until pending empty, kicks drain if needed
- maxConsecutiveFailures: drop batch and advance after N failures
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Throw from send() to override exponential backoff with server-supplied delay."""

    def __init__(self, message: str, retry_after_ms: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class SerialBatchEventUploader(Generic[T]):
    def __init__(
        self,
        *,
        max_batch_size: int,
        max_queue_size: int,
        send: Callable[[list[T]], Any],
        base_delay_ms: float,
        max_delay_ms: float,
        jitter_ms: float,
        max_batch_bytes: int | None = None,
        max_consecutive_failures: int | None = None,
        on_batch_dropped: Any | None = None,
    ) -> None:
        self._max_batch_size = max_batch_size
        self._max_queue_size = max_queue_size
        self._max_batch_bytes = max_batch_bytes
        self._send = send
        self._base = base_delay_ms
        self._max = max_delay_ms
        self._jitter = jitter_ms
        self._max_consecutive_failures = max_consecutive_failures
        self._on_batch_dropped = on_batch_dropped

        self._pending: list[T] = []
        self._pending_at_close = 0
        self._draining = False
        self._closed = False
        self._backpressure_resolvers: list[Callable[[], None]] = []
        self._flush_resolvers: list[Callable[[], None]] = []
        self._dropped_batches = 0
        self._sleep_resolve: Callable[[], None] | None = None

    @property
    def dropped_batch_count(self) -> int:
        return self._dropped_batches

    @property
    def pending_count(self) -> int:
        return self._pending_at_close if self._closed else len(self._pending)

    async def enqueue(self, events: T | list[T]) -> None:
        """Add events. Blocks if queue is full (backpressure)."""
        items = events if isinstance(events, list) else [events]
        while len(self._pending) + len(items) > self._max_queue_size:
            if self._closed:
                return
            # Wait for drain to free space
            waiter: asyncio.Future[None] = asyncio.Future()
            self._backpressure_resolvers.append(
                lambda: waiter.set_result(None) if not waiter.done() else None
            )
            await waiter
        self._pending.extend(items)
        if not self._draining:
            asyncio.ensure_future(self._drain())

    async def flush(self) -> None:
        """Block until all pending events are sent."""
        if self._pending:
            if not self._draining:
                asyncio.ensure_future(self._drain())
            waiter: asyncio.Future[None] = asyncio.Future()
            self._flush_resolvers.append(
                lambda: waiter.set_result(None) if not waiter.done() else None
            )
            await waiter
        # Drain may have stopped; kick once more for anything enqueued during await
        if self._pending:
            if not self._draining:
                asyncio.ensure_future(self._drain())
            waiter2: asyncio.Future[None] = asyncio.Future()
            self._flush_resolvers.append(
                lambda: waiter2.set_result(None) if not waiter2.done() else None
            )
            await waiter2

    def close(self) -> None:
        """Stop accepting new events, clear pending, release all waiters."""
        self._closed = True
        self._pending_at_close = len(self._pending)
        self._pending.clear()
        self._release_backpressure()
        self._release_flush()

    async def _drain(self) -> None:
        if self._draining or self._closed:
            return
        self._draining = True
        failures = 0
        try:
            while self._pending and not self._closed:
                batch = self._take_batch()
                if not batch:
                    break
                try:
                    await self._send(batch)
                    failures = 0
                    self._release_backpressure()
                except RetryableError as e:
                    failures += 1
                    if (
                        self._max_consecutive_failures
                        and failures >= self._max_consecutive_failures
                    ):
                        self._dropped_batches += 1
                        if self._on_batch_dropped:
                            self._on_batch_dropped(len(batch), failures)
                        failures = 0
                        continue
                    delay = self._compute_delay(failures, e.retry_after_ms)
                    await self._sleep(delay)
                    self._pending = list(batch) + self._pending
                except Exception:
                    failures += 1
                    if (
                        self._max_consecutive_failures
                        and failures >= self._max_consecutive_failures
                    ):
                        self._dropped_batches += 1
                        if self._on_batch_dropped:
                            self._on_batch_dropped(len(batch), failures)
                        failures = 0
                        continue
                    delay = self._compute_delay(failures)
                    await self._sleep(delay)
                    self._pending = list(batch) + self._pending
        finally:
            self._draining = False
            self._release_flush()

    def _take_batch(self) -> list[T]:
        """Take up to maxBatchSize items, respecting maxBatchBytes."""
        count = min(len(self._pending), self._max_batch_size)
        if self._max_batch_bytes is not None and count > 1:
            # Byte-size capping: first item always goes in
            batch_bytes = len(self._serialize(self._pending[0]))
            actual_count = 1
            for i in range(1, count):
                item_bytes = len(self._serialize(self._pending[i]))
                if batch_bytes + item_bytes > self._max_batch_bytes:
                    break
                batch_bytes += item_bytes
                actual_count += 1
            count = actual_count
        batch = self._pending[:count]
        self._pending = self._pending[count:]
        return batch

    def _serialize(self, item: T) -> str:
        if isinstance(item, str):
            return item
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    def _compute_delay(
        self, failures: int, retry_after_ms: float | None = None
    ) -> float:
        if retry_after_ms is not None:
            raw = max(self._base, min(retry_after_ms, self._max))
        else:
            raw = min(self._base * (2 ** (failures - 1)), self._max)
        jitter = random.uniform(0, self._jitter)
        return (raw + jitter) / 1000.0

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            pass

    def _release_backpressure(self) -> None:
        resolvers = self._backpressure_resolvers
        self._backpressure_resolvers = []
        for r in resolvers:
            r()

    def _release_flush(self) -> None:
        resolvers = self._flush_resolvers
        self._flush_resolvers = []
        for r in resolvers:
            r()
