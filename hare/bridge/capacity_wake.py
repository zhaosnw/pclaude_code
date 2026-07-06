"""
Capacity wake — signal mechanism for session capacity management.

Port of: src/bridge/capacityWake.ts

Used to wake the poll loop when session capacity frees up.
Supports dual-signal pattern (outer abort + internal wake).
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional


class CapacityWake:
    """Signal-based wake mechanism for poll loop."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._outer_signal: Any = None

    def set_outer_signal(self, signal: Any) -> None:
        """Set an outer abort signal (e.g., shutdown)."""
        self._outer_signal = signal

    def signal(self) -> None:
        """Wake the poll loop."""
        self._event.set()

    async def wait(self, timeout_ms: Optional[float] = None) -> bool:
        """Wait for wake signal with optional timeout.

        Returns True if woken by signal, False if timeout.
        """
        try:
            if timeout_ms is not None:
                timeout = timeout_ms / 1000.0
                await asyncio.wait_for(self._wait_inner(), timeout)
            else:
                await self._wait_inner()
            self._event.clear()
            return True
        except asyncio.TimeoutError:
            return False

    async def _wait_inner(self) -> None:
        """Wait for inner event, optionally watching outer signal."""
        if self._outer_signal:
            # If outer signal is set, stop waiting
            while not self._event.is_set():
                if (
                    hasattr(self._outer_signal, "aborted")
                    and self._outer_signal.aborted
                ):
                    return
                await asyncio.sleep(0.1)
                if self._event.is_set():
                    return
        else:
            await self._event.wait()

    def reset(self) -> None:
        """Reset the wake signal."""
        self._event.clear()


def create_capacity_wake() -> CapacityWake:
    return CapacityWake()
