"""
File lock helper for task/mailbox-style serialization.

Port of: src/utils/lockfile.js (proper-lockfile behavior).

Single-process stub: uses an asyncio.Lock registry keyed by path.
For multi-process isolation, replace with a real file lock (e.g. filelock).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def lock(
    path: str,
    _options: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> Callable[[], Awaitable[None]]:
    """Acquire a lock for ``path``. Returns an async release function."""

    lk = _locks[path]
    await lk.acquire()

    async def release() -> None:
        lk.release()

    return release
