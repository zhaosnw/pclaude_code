"""
/stickers command - manage sticker reactions.

Port of: src/commands/stickers/ (2 files)

In the TS CLI this shows a sticker picker for reacting to messages.
In the headless SDK, it shows available sticker options.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "stickers"
DESCRIPTION = "Manage sticker reactions"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show sticker management info."""
    list_stickers = context.get("list_stickers")

    stickers = []
    if list_stickers:
        stickers = list_stickers()

    if stickers:
        lines = ["## Available Stickers", ""]
        for s in stickers:
            lines.append(
                f"- {s.get('emoji', '')} **{s.get('name', '')}** — {s.get('description', '')}"
            )
        return {"type": "text", "value": "\n".join(lines)}

    return {
        "type": "text",
        "value": (
            "## Stickers\n\n"
            "Sticker reactions are available in the interactive CLI.\n"
            "Use the sticker picker in the chat interface to react to messages."
        ),
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
