"""Async push stream (port of stream.ts)."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Generic, TypeVar

T = TypeVar("T")

_END = object()


class Stream(Generic[T], AsyncIterator[T]):
    """Single-consumer async queue with completion and error signals."""

    def __init__(self, returned: Any = None) -> None:
        self._queue: asyncio.Queue[T | object] = asyncio.Queue()
        self._is_done = False
        self._has_error: BaseException | None = None
        self._started = False
        self._returned = returned

    def __aiter__(self) -> Stream[T]:
        if self._started:
            raise RuntimeError("Stream can only be iterated once")
        self._started = True
        return self

    async def __anext__(self) -> T:
        if self._has_error is not None:
            raise self._has_error
        item = await self._queue.get()
        if item is _END:
            raise StopAsyncIteration
        return item  # type: ignore[return-value]

    def enqueue(self, value: T) -> None:
        self._queue.put_nowait(value)

    def done(self) -> None:
        self._is_done = True
        self._queue.put_nowait(_END)

    def error(self, err: BaseException) -> None:
        self._has_error = err
        self._queue.put_nowait(_END)

    async def aclose(self) -> None:
        self.done()
        if self._returned:
            self._returned()
