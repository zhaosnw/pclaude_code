"""
/statusline command - configure the CLI status line.

Port of: src/commands/statusline.tsx

In the TS CLI, this spawns an Agent subagent with type "statusline-setup"
to configure the terminal status line. In the headless SDK, it returns
configuration guidance.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "statusline"
DESCRIPTION = "Configure the CLI status line"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Configure status line."""
    run_statusline_setup = context.get("run_statusline_setup")

    if run_statusline_setup:
        try:
            result = await run_statusline_setup(args.strip() if args else None)
            return {
                "type": "text",
                "value": result.get("message", "Status line configured."),
            }
        except Exception as e:
            return {
                "type": "text",
                "value": f"Status line setup failed: {e}",
                "display": "system",
            }

    hint = args.strip() if args else "default"

    return {
        "type": "text",
        "value": (
            "## Status Line Configuration\n\n"
            f"Request: {hint}\n\n"
            "The status line is configured via `~/.claude/settings.json`:\n\n"
            "```json\n"
            "{{\n"
            '  "statusLine": {{\n'
            '    "type": "ascii",\n'
            '    "position": "right"\n'
            "  }}\n"
            "}}\n"
            "```\n\n"
            "Use `/config` to modify settings or edit `.claude/settings.local.json`."
        ),
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[configuration]",
        "call": call,
    }
