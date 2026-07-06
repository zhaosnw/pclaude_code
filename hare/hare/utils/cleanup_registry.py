"""Global registry for graceful shutdown cleanup callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

_cleanup_functions: set[Callable[[], Awaitable[None]]] = set()


def register_cleanup(cleanup_fn: Callable[[], Awaitable[None]]) -> Callable[[], None]:
    """Register an async cleanup; returns unregister callable."""
    _cleanup_functions.add(cleanup_fn)

    def unregister() -> None:
        _cleanup_functions.discard(cleanup_fn)

    return unregister


async def run_cleanup() -> None:
    """Run all registered cleanup functions (TS parity alias)."""
    await run_cleanup_functions()


async def run_cleanup_functions() -> None:
    await asyncio.gather(*(fn() for fn in _cleanup_functions))
