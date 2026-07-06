"""Async-friendly mailbox — port of `mailbox.ts`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Literal

from hare.utils.signal import create_signal

MessageSource = Literal["user", "teammate", "system", "tick", "task"]


@dataclass
class Message:
    id: str
    source: MessageSource
    content: str
    timestamp: str
    from_name: str | None = None
    color: str | None = None


class _Waiter:
    __slots__ = ("fn", "resolve")

    def __init__(
        self, fn: Callable[[Message], bool], resolve: Callable[[Message], None]
    ) -> None:
        self.fn = fn
        self.resolve = resolve


class Mailbox:
    def __init__(self) -> None:
        self._queue: list[Message] = []
        self._waiters: list[_Waiter] = []
        self._changed = create_signal()
        self._revision = 0

    @property
    def length(self) -> int:
        return len(self._queue)

    @property
    def revision(self) -> int:
        return self._revision

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        return self._changed.subscribe(listener)

    def send(self, msg: Message) -> None:
        self._revision += 1
        idx = next((i for i, w in enumerate(self._waiters) if w.fn(msg)), -1)
        if idx != -1:
            waiter = self._waiters.pop(idx)
            waiter.resolve(msg)
            self._notify()
            return
        self._queue.append(msg)
        self._notify()

    def poll(self, fn: Callable[[Message], bool] | None = None) -> Message | None:
        pred = fn or (lambda _m: True)
        for i, m in enumerate(self._queue):
            if pred(m):
                return self._queue.pop(i)
        return None

    async def receive(self, fn: Callable[[Message], bool] | None = None) -> Message:
        pred = fn or (lambda _m: True)
        for i, m in enumerate(self._queue):
            if pred(m):
                del self._queue[i]
                self._notify()
                return m

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Message] = loop.create_future()

        def resolve(msg: Message) -> None:
            if not fut.done():
                fut.set_result(msg)

        self._waiters.append(_Waiter(pred, resolve))
        return await fut

    def _notify(self) -> None:
        self._changed.emit()
