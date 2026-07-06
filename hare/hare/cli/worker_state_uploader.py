"""
Worker state uploader with coalescing and inflight guard.

Port of: src/cli/transports/WorkerStateUploader.ts

Coalesces rapid state updates: top-level keys last-write-wins,
metadata keys merge via RFC 7396 semantics.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Callable


class WorkerStateUploader:
    def __init__(
        self,
        *,
        send: Callable[[dict[str, Any]], Any],
        base_delay_ms: float = 1000,
        max_delay_ms: float = 30000,
        jitter_ms: float = 500,
    ) -> None:
        self._send = send
        self._base = base_delay_ms
        self._max = max_delay_ms
        self._jitter = jitter_ms
        self._pending: dict[str, Any] | None = None
        self._draining = False
        self._closed = False
        self._metadata_base: dict[str, Any] = {}

    def enqueue(self, patch: dict[str, Any]) -> None:
        """Queue a state update. Coalesces: top-level keys last-wins,
        metadata keys RFC 7396 merge."""
        if self._closed:
            return

        # Split top-level from metadata
        top_keys = {k: v for k, v in patch.items() if k != "metadata"}
        meta = patch.get("metadata")

        if self._pending is None:
            self._pending = dict(top_keys)
        else:
            self._pending.update(top_keys)

        # RFC 7396 merge for metadata
        if isinstance(meta, dict):
            cur = self._pending.setdefault("metadata", {})
            if isinstance(cur, dict):
                for k, v in meta.items():
                    if v is None:
                        cur.pop(k, None)
                    else:
                        cur[k] = v

        if not self._draining:
            asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        """Send pending state. At most one inflight at a time."""
        if self._draining or self._closed:
            return
        self._draining = True
        failures = 0
        try:
            while self._pending is not None and not self._closed:
                payload = self._pending
                self._pending = None  # capture and clear — new updates coalesce fresh
                try:
                    await self._send(payload)
                    failures = 0
                except Exception:
                    failures += 1
                    # Restore pending for retry
                    self._pending = {**(self._pending or {}), **payload}
                    delay = self._compute_delay(failures)
                    await asyncio.sleep(delay)
        finally:
            self._draining = False

    def _compute_delay(self, failures: int) -> float:
        raw = min(self._base * (2 ** (failures - 1)), self._max)
        jitter = random.uniform(0, self._jitter)
        return (raw + jitter) / 1000.0

    def close(self) -> None:
        self._closed = True
        self._pending = None
