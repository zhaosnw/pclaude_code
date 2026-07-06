"""Port of: src/commands/autofix-pr/ — SDK non-interactive stub."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "autofix-pr"
DESCRIPTION = "Autofix PR workflow (internal in TS)."
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    return {
        "type": "text",
        "value": (
            "`/autofix-pr` (SDK stub): full behavior is in recovered TypeScript; "
            "this headless path only records the request. Args: {args!r}".format(
                args=args.strip() or "(none)"
            )
        ),
    }
