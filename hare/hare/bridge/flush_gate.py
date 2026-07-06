"""
FlushGate — queue messages during history flush to avoid interleaving.

Port of: src/bridge/flushGate.ts

While a history flush is in progress, new messages are queued.
When the flush ends, queued messages are drained and returned.
Callers should check enqueue()'s boolean return to decide whether
to send directly or wait for drain.
"""

from __future__ import annotations

from typing import Any


class FlushGate:
    """Queue/drain gate for history flush coordination."""

    def __init__(self) -> None:
        self._active = False
        self._queue: list[Any] = []
        self._pending_count = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def pending_count(self) -> int:
        return self._pending_count

    def start(self) -> None:
        """Begin a flush — subsequent messages will be queued."""
        self._active = True

    def end(self) -> list[Any]:
        """End the flush and return all queued messages."""
        self._active = False
        drained = list(self._queue)
        self._queue.clear()
        self._pending_count = 0
        return drained

    def enqueue(self, *items: Any) -> bool:
        """Queue items during a flush. Returns True if items were queued
        (flush was active), False otherwise (caller should send directly)."""
        if self._active:
            self._queue.extend(items)
            self._pending_count += len(items)
            return True
        return False

    def drop(self) -> int:
        """Drop all queued messages. Returns count of dropped items."""
        count = len(self._queue)
        self._queue.clear()
        self._pending_count = 0
        return count

    def deactivate(self) -> None:
        """Deactivate the gate entirely (teardown)."""
        self._active = False
        self._queue.clear()
        self._pending_count = 0
