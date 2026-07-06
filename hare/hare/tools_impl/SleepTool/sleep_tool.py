"""
SleepTool – wait for a duration (no TS implementation file, only prompt).

Port of: src/tools/SleepTool/ (prompt-only in TS; implementation in TSX)
"""

from __future__ import annotations
import asyncio
from typing import Any

TOOL_NAME = "Sleep"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "duration_ms": {
                "type": "number",
                "description": "Duration in milliseconds",
            },
        },
        "required": ["duration_ms"],
    }


async def call(duration_ms: int = 5000, **kwargs: Any) -> dict[str, Any]:
    seconds = max(0, duration_ms / 1000)
    await asyncio.sleep(seconds)
    return {"data": f"Slept for {seconds:.1f}s"}
