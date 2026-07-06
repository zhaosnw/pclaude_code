"""
/mobile command - manage mobile app pairing.

Port of: src/commands/mobile/ (2 files)

Shows mobile app connection status and pairing QR code.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "mobile"
DESCRIPTION = "Manage mobile app connection"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show mobile connection status."""
    get_mobile_status = context.get("get_mobile_status")

    if get_mobile_status:
        status = await get_mobile_status()
    else:
        status = {"paired": False}

    lines = ["## Mobile App", ""]

    if status.get("paired"):
        lines.append("**Status:** Paired")
        lines.append(f"**Device:** {status.get('device_name', 'Unknown')}")
        lines.append(f"**Last connected:** {status.get('last_connected', 'Unknown')}")
    else:
        lines.append("**Status:** Not paired")
        lines.append("")
        lines.append("Download the Claude Code mobile app:")
        lines.append("- **iOS:** https://apps.apple.com/app/claude-code/...")
        lines.append("- **Android:** https://play.google.com/store/apps/...")
        lines.append("")
        lines.append("Use `/session` to get a QR code for mobile pairing.")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
