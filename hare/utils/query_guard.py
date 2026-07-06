"""
Synchronous state machine for the query lifecycle (React useSyncExternalStore compatible).

Port of: src/utils/QueryGuard.ts
"""

from __future__ import annotations

from typing import Callable

from hare.utils.signal import create_signal


class QueryGuard:
    """idle | dispatching | running — prevents re-entry during async gaps."""

    def __init__(self) -> None:
        self._status: str = "idle"
        self._generation = 0
        self._changed = create_signal()
        self.subscribe: Callable[[Callable[[], None]], Callable[[], None]] = (
            self._changed.subscribe
        )

    def reserve(self) -> bool:
        if self._status != "idle":
            return False
        self._status = "dispatching"
        self._notify()
        return True

    def cancel_reservation(self) -> None:
        if self._status != "dispatching":
            return
        self._status = "idle"
        self._notify()

    def try_start(self) -> int | None:
        if self._status == "running":
            return None
        self._status = "running"
        self._generation += 1
        self._notify()
        return self._generation

    def end(self, generation: int) -> bool:
        if self._generation != generation:
            return False
        if self._status != "running":
            return False
        self._status = "idle"
        self._notify()
        return True

    def force_end(self) -> None:
        if self._status == "idle":
            return
        self._status = "idle"
        self._generation += 1
        self._notify()

    @property
    def is_active(self) -> bool:
        return self._status != "idle"

    @property
    def generation(self) -> int:
        return self._generation

    def get_snapshot(self) -> bool:
        return self._status != "idle"

    def _notify(self) -> None:
        self._changed.emit()
