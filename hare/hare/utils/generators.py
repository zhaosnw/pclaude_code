"""Async generator utilities.

Port of: src/utils/generators.ts (line-by-line; symbol names snake_cased).
"""

from __future__ import annotations

import asyncio
import math
from typing import AsyncIterator, AsyncGenerator, Sequence, TypeVar

A = TypeVar("A")
T = TypeVar("T")

# Module-private sentinel — TS uses `Symbol('NO_VALUE')` (line 1).
_NO_VALUE: object = object()


# -- src/utils/generators.ts L3-12
async def last_x(gen: AsyncIterator[A]) -> A:
    last_value: object = _NO_VALUE
    async for a in gen:
        last_value = a
    if last_value is _NO_VALUE:
        raise RuntimeError("No items in generator")
    return last_value  # type: ignore[return-value]


# -- src/utils/generators.ts L14-22
#
# `returnValue` consumes a generator and returns its `return` value (the
# `value` field when `done=True`). Python async generators don't naturally
# carry a return value the same way; `async for` swallows it. We drive
# `__anext__` manually and capture `StopAsyncIteration.value`.
async def return_value(gen: AsyncGenerator[object, A]) -> A:
    while True:
        try:
            await gen.__anext__()
        except StopAsyncIteration as stop:
            return stop.value  # type: ignore[return-value]


# -- src/utils/generators.ts L24-29 (QueuedGenerator type) is implicit in Python.


# -- src/utils/generators.ts L31-72
#
# Run all generators concurrently up to a concurrency cap, yielding values as
# they come in. Mirrors the TS Promise.race-based scheduler: once a generator
# yields, schedule its next() and surface the value; if a generator finishes
# and there are pending generators in the waiting queue, start one.
async def all(  # noqa: A001 — name matches TS export `all`
    generators: Sequence[AsyncGenerator[A, None]],
    concurrency_cap: float = math.inf,
) -> AsyncGenerator[A, None]:
    waiting: list[AsyncGenerator[A, None]] = list(generators)

    # Map task -> (generator, promise-like payload sentinel) so we can
    # distinguish an explicit `None` yield from generator completion.
    tasks: dict[asyncio.Task, AsyncGenerator[A, None]] = {}

    def schedule(gen: AsyncGenerator[A, None]) -> None:
        task = asyncio.ensure_future(gen.__anext__())
        tasks[task] = gen

    while len(tasks) < concurrency_cap and waiting:
        schedule(waiting.pop(0))

    while tasks:
        done, _pending = await asyncio.wait(
            tasks.keys(), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            gen = tasks.pop(task)
            try:
                value = task.result()
            except StopAsyncIteration:
                # Generator finished — start a waiting one if any.
                if waiting:
                    schedule(waiting.pop(0))
                continue
            # Schedule generator's next iteration before yielding to mirror
            # TS semantics (the TS code adds the next promise to the set
            # before the `yield value`). Order matters under concurrency.
            schedule(gen)
            yield value


# -- src/utils/generators.ts L74-82
async def to_array(generator: AsyncGenerator[A, None]) -> list[A]:
    result: list[A] = []
    async for a in generator:
        result.append(a)
    return result


# -- src/utils/generators.ts L84-88
async def from_array(values: Sequence[T]) -> AsyncGenerator[T, None]:
    for value in values:
        yield value
