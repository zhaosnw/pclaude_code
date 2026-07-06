"""Port of: src/commands/effort.ts"""

from __future__ import annotations
from typing import Any

COMMAND_NAME = "effort"
DESCRIPTION = "Set the effort level for responses (low, medium, high, max)"
ALIASES: list[str] = []

VALID_EFFORTS = {"low", "medium", "high", "max"}


async def call(args: str, messages: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
    level = args.strip().lower()

    if not level:
        current = context.get("effort", context.get("current_effort", "medium"))
        return {"type": "text",
                "value": f"Current effort level: **{current}**\n\n"
                         f"Usage: /effort <{' | '.join(sorted(VALID_EFFORTS))}>\n"
                         f"- **low**: Minimal reasoning, faster\n"
                         f"- **medium**: Balanced (default)\n"
                         f"- **high**: Thorough analysis\n"
                         f"- **max**: Maximum reasoning depth"}

    if level not in VALID_EFFORTS:
        return {"type": "error",
                "display_text": f"Invalid: '{level}'. Choose: {', '.join(sorted(VALID_EFFORTS))}"}

    for key in ("set_effort", "setEffort"):
        setter = context.get(key)
        if setter and callable(setter):
            try:
                result = setter(level)
                import asyncio
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    return {"type": "effort", "effort": level, "display_text": f"Effort level set to: **{level}**"}
