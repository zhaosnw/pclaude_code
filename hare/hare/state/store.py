"""Port of: src/state/store.ts"""

from __future__ import annotations
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


class Store(Generic[T]):
    def __init__(self, initial: T) -> None:
        self._state = initial
        self._listeners: list[Callable[[T], None]] = []

    def get_state(self) -> T:
        return self._state

    def set_state(self, updater: Callable[[T], T]) -> None:
        self._state = updater(self._state)
        for listener in self._listeners:
            listener(self._state)

    def subscribe(self, listener: Callable[[T], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsub():
            self._listeners.remove(listener)

        return unsub


def create_store(initial: Any) -> Store:
    return Store(initial)
