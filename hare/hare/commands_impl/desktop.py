"""
/desktop command - manage desktop app integration.

Port of: src/commands/desktop/ (2 files)

Shows desktop app connection status and setup instructions.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "desktop"
DESCRIPTION = "Manage desktop app integration"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show desktop app status."""
    get_desktop_status = context.get("get_desktop_status")

    if get_desktop_status:
        status = await get_desktop_status()
    else:
        status = {"connected": False}

    lines = ["## Desktop App", ""]

    if status.get("connected"):
        lines.append("**Status:** Connected to desktop app")
        lines.append(f"**App version:** {status.get('version', 'unknown')}")
    else:
        lines.append("**Status:** Not connected")
        lines.append("")
        lines.append("Download the Claude Code desktop app:")
        lines.append("https://claude.ai/download")
        lines.append("")
        lines.append("The desktop app provides a native UI with file system access.")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
