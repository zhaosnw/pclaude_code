"""TTL and LRU memoization — port of `memoize.ts`."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, TypeVar

from hare.utils.log import log_error

R = TypeVar("R")


def _key(args: tuple[Any, ...]) -> str:
    return json.dumps(args, default=str)


def memoize_with_ttl(
    f: Callable[..., R], cache_lifetime_ms: int = 5 * 60 * 1000
) -> Callable[..., R]:
    cache: dict[str, list[Any]] = {}

    def memoized(*args: Any) -> R:
        k = _key(args)
        now = time.time() * 1000
        ent = cache.get(k)
        if ent is None:
            v = f(*args)
            cache[k] = [v, now, False]
            return v
        v, ts, refreshing = ent[0], ent[1], ent[2]
        if now - ts <= cache_lifetime_ms:
            return v
        if not refreshing:
            ent[2] = True

            def refresh() -> None:
                try:
                    nv = f(*args)
                    cur = cache.get(k)
                    if cur is ent:
                        ent[0] = nv
                        ent[1] = time.time() * 1000
                        ent[2] = False
                except Exception as e:
                    log_error(e)
                    cur = cache.get(k)
                    if cur is ent:
                        del cache[k]

            threading.Thread(target=refresh, daemon=True).start()
        return v

    memoized.cache = type("C", (), {"clear": lambda: cache.clear()})()
    return memoized  # type: ignore[return-value]


def memoize_with_ttl_async(
    f: Callable[..., Any],
    cache_lifetime_ms: int = 5 * 60 * 1000,
) -> Callable[..., Any]:
    cache: dict[str, list[Any]] = {}
    in_flight: dict[str, asyncio.Future[Any]] = {}

    async def memoized(*args: Any) -> Any:
        k = _key(args)
        now = time.time() * 1000
        ent = cache.get(k)
        if ent is None:
            if k in in_flight:
                return await in_flight[k]
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[Any] = loop.create_future()
            in_flight[k] = fut
            try:
                result = await f(*args)
                cache[k] = [result, now, False]
                fut.set_result(result)
                return result
            except Exception as e:
                if not fut.done():
                    fut.set_exception(e)
                raise
            finally:
                in_flight.pop(k, None)

        v, ts, refreshing = ent[0], ent[1], ent[2]
        if now - ts <= cache_lifetime_ms:
            return v
        if not refreshing:
            ent[2] = True
            stale = ent

            async def refresh() -> None:
                try:
                    nv = await f(*args)
                    cur = cache.get(k)
                    if cur is stale:
                        ent[0] = nv
                        ent[1] = time.time() * 1000
                        ent[2] = False
                except Exception as e:
                    log_error(e)
                    if k in cache and cache[k] is stale:
                        del cache[k]

            asyncio.create_task(refresh())
        return v

    def clear() -> None:
        cache.clear()
        in_flight.clear()

    memoized.cache = type("C", (), {"clear": clear})()
    return memoized


def memoize_with_lru(
    f: Callable[..., R],
    cache_fn: Callable[..., str],
    max_cache_size: int = 100,
) -> Callable[..., R]:
    cache: OrderedDict[str, R] = OrderedDict()

    def memoized(*args: Any) -> R:
        key = cache_fn(*args)
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        result = f(*args)
        cache[key] = result
        if len(cache) > max_cache_size:
            cache.popitem(last=False)
        return result

    class _Cache:
        def clear(self) -> None:
            cache.clear()

        def size(self) -> int:
            return len(cache)

        def delete(self, key: str) -> bool:
            return cache.pop(key, None) is not None

        def get(self, key: str) -> R | None:
            return cache.get(key)

        def has(self, key: str) -> bool:
            return key in cache

    memoized.cache = _Cache()  # type: ignore[attr-defined]
    return memoized  # type: ignore[return-value]
