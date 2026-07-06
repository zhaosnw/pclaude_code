"""Port of: src/commands/bughunter/ — SDK non-interactive stub."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "bughunter"
DESCRIPTION = "Bughunter integration (internal)."
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    return {
        "type": "text",
        "value": (
            "`/bughunter` (SDK stub): full behavior is in recovered TypeScript; "
            "this headless path only records the request. Args: {args!r}".format(
                args=args.strip() or "(none)"
            )
        ),
    }
