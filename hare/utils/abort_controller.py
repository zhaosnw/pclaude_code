"""AbortController / AbortSignal compatible with combined_abort_signal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AbortController:
    _signal: "AbortSignal" = field(init=False)

    def __post_init__(self) -> None:
        self._signal = AbortSignal()

    @property
    def signal(self) -> "AbortSignal":
        return self._signal

    def abort(self) -> None:
        self._signal._trigger_abort()  # noqa: SLF001


class AbortSignal:
    def __init__(self) -> None:
        self._aborted = False
        self._abort_callbacks: list[Callable[[], None]] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    def add_event_listener(self, name: str, cb: Callable[[], None]) -> None:
        if name == "abort":
            self._abort_callbacks.append(cb)

    def remove_event_listener(self, name: str, cb: Callable[[], None]) -> None:
        if name == "abort":
            try:
                self._abort_callbacks.remove(cb)
            except ValueError:
                pass

    def _trigger_abort(self) -> None:
        if self._aborted:
            return
        self._aborted = True
        for cb in list(self._abort_callbacks):
            cb()


def create_abort_controller() -> AbortController:
    return AbortController()
