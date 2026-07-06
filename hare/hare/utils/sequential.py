"""
Serialize concurrent async calls. Port of src/utils/sequential.ts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

R = TypeVar("R")


def sequential(fn: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
    q: list[tuple[tuple[Any, ...], dict[str, Any], asyncio.Future[R]]] = []
    processing = False

    async def process_queue() -> None:
        nonlocal processing
        if processing:
            return
        processing = True
        try:
            while q:
                args, kw, fut = q.pop(0)
                try:
                    res = await fn(*args, **kw)
                    fut.set_result(res)
                except BaseException as e:
                    fut.set_exception(e)
        finally:
            processing = False
            if q:
                asyncio.create_task(process_queue())

    async def wrapped(*args: Any, **kwargs: Any) -> R:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[R] = loop.create_future()
        q.append((args, kwargs, fut))
        asyncio.create_task(process_queue())
        return await fut

    return wrapped
