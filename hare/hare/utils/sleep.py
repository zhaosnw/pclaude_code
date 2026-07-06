"""Port of: src/utils/sleep.ts"""

from __future__ import annotations
import asyncio


async def sleep(ms: float) -> None:
    await asyncio.sleep(ms / 1000)
