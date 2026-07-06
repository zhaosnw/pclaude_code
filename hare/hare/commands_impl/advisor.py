"""
Advisor command — inline suggestions / review.

Port of: src/commands/advisor.ts
"""

from __future__ import annotations

from typing import Any


COMMAND_NAME = "advisor"
DESCRIPTION = "Inline advisor / suggestion mode"
ALIASES: list[str] = []


async def run_advisor_command(_args: list[str]) -> dict[str, Any]:
    return {"ok": True}


async def call(args: str, **context: Any) -> dict[str, Any]:
    await run_advisor_command([])
    return {"type": "text", "value": "Advisor (SDK stub)."}
