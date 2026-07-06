"""Port of: src/commands/ant-trace/ — SDK non-interactive stub."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "ant-trace"
DESCRIPTION = "Internal Ant trace (TS stub / hidden)."
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    return {
        "type": "text",
        "value": (
            "`/ant-trace` (SDK stub): full behavior is in recovered TypeScript; "
            "this headless path only records the request. Args: {args!r}".format(
                args=args.strip() or "(none)"
            )
        ),
    }
