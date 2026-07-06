"""Port of: src/commands/terminalSetup/. Terminal integration setup."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "terminal-setup"
DESCRIPTION = "Configure terminal integration for the CLI"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    return {
        "type": "text",
        "value": "Terminal setup (SDK stub): full wizard is Ink-only in recovered TS.",
    }
