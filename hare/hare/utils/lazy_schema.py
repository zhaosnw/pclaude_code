"""Defer schema construction until first access — port of `src/utils/lazySchema.ts`."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def lazy_schema(factory: Callable[[], T]) -> Callable[[], T]:
    """Memoized factory: constructs value on first call only."""
    cached: T | None = None

    def getter() -> T:
        nonlocal cached
        if cached is None:
            cached = factory()
        return cached

    return getter
