"""
Non-interactive context command.

Port of: src/commands/context/context-noninteractive.ts
"""

from __future__ import annotations

from typing import Any


async def run_context_noninteractive(_args: list[str]) -> dict[str, Any]:
    return {"ok": True}
