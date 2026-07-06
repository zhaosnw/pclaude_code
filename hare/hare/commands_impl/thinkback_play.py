"""
/thinkback-play command - play back a thinkback recording.

Port of: src/commands/thinkback-play/thinkback-play.ts + index.ts

Plays back a recorded thinking session. In the TS CLI this is interactive.
In the headless SDK, it shows available recordings.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "thinkback-play"
DESCRIPTION = "Play back a thinkback recording"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Play back a recorded thinkback session."""
    get_thinkback_recordings = context.get("get_thinkback_recordings")

    recordings = []
    if get_thinkback_recordings:
        recordings = await get_thinkback_recordings()

    if not recordings:
        return {
            "type": "text",
            "value": (
                "No thinkback recordings found.\n\n"
                "Recordings are created when extended thinking is enabled.\n"
                "Use `/think-back` to view thinking blocks from the current session."
            ),
        }

    lines = ["## Thinkback Recordings", ""]
    for i, rec in enumerate(recordings):
        date = rec.get("date", "unknown")
        preview = rec.get("firstPrompt", rec.get("title", "untitled"))[:80]
        lines.append(f"**[{i}]** {date} — {preview}")

    lines.extend(["", "Use `/thinkback-play <index>` to replay a specific recording."])
    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[index]",
        "call": call,
    }
