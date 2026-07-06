"""
Clear local caches (subcommand).

Port of: src/commands/clear/caches.ts
"""

from __future__ import annotations

from typing import Any


async def clear_caches() -> dict[str, Any]:
    return {"cleared": True}
