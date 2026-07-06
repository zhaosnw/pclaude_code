"""
/chrome command - manage Claude in Chrome extension integration.

Port of: src/commands/chrome/ (2 files)

Manages the Claude in Chrome extension connection.
In the TS CLI this shows an interactive dialog.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "chrome"
DESCRIPTION = "Manage Claude in Chrome integration"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show Chrome integration status and management options."""
    get_chrome_status = context.get("get_chrome_status")
    get_app_state = context.get("get_app_state")

    if get_chrome_status:
        status = await get_chrome_status()
    else:
        status = {"connected": False, "installed": False}

    lines = ["## Claude in Chrome", ""]

    if status.get("installed"):
        if status.get("connected"):
            lines.append("**Status:** Connected")
            lines.append(f"**Extension version:** {status.get('version', 'unknown')}")
        else:
            lines.append("**Status:** Extension installed but not connected")
            lines.append(
                "Make sure the extension is enabled and the bridge is running."
            )
    else:
        lines.append("**Status:** Not installed")
        lines.append("")
        lines.append("Install the Claude in Chrome extension:")
        lines.append("https://chrome.google.com/webstore (search for 'Claude Code')")

    lines.append("")
    lines.append("The Chrome extension allows Claude to interact with browser content.")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
