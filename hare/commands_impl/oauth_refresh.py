"""Port of: src/commands/oauth-refresh/ — SDK non-interactive stub."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "oauth-refresh"
DESCRIPTION = "OAuth token refresh (internal)."
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    return {
        "type": "text",
        "value": (
            "`/oauth-refresh` (SDK stub): full behavior is in recovered TypeScript; "
            "this headless path only records the request. Args: {args!r}".format(
                args=args.strip() or "(none)"
            )
        ),
    }
