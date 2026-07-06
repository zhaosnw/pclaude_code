"""Fixed-size circular buffer (`CircularBuffer.ts`)."""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class CircularBuffer(Generic[T]):
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._buffer: list[T | None] = [None] * capacity
        self._head = 0
        self._size = 0

    def add(self, item: T) -> None:
        self._buffer[self._head] = item
        self._head = (self._head + 1) % self._capacity
        if self._size < self._capacity:
            self._size += 1

    def add_all(self, items: list[T]) -> None:
        for it in items:
            self.add(it)

    def get_recent(self, count: int) -> list[T]:
        result: list[T] = []
        start = 0 if self._size < self._capacity else self._head
        available = min(count, self._size)
        for i in range(available):
            idx = (start + self._size - available + i) % self._capacity
            val = self._buffer[idx]
            if val is not None:
                result.append(val)
        return result

    def to_array(self) -> list[T]:
        if self._size == 0:
            return []
        start = 0 if self._size < self._capacity else self._head
        out: list[T] = []
        for i in range(self._size):
            idx = (start + i) % self._capacity
            val = self._buffer[idx]
            if val is not None:
                out.append(val)
        return out

    def clear(self) -> None:
        self._buffer = [None] * self._capacity
        self._head = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size
