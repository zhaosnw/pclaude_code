"""
/session command - show current session information.

Port of: src/commands/session/session.tsx + index.ts

Shows session ID, remote session URL (with QR support), and session metadata.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "session"
DESCRIPTION = "Show current session information"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Display session information."""
    get_session_id = context.get("get_session_id")
    get_app_state = context.get("get_app_state")
    session_id = get_session_id() if get_session_id else "unknown"

    lines = [
        "## Session Info",
        "",
        f"**Session ID:** {session_id}",
    ]

    if get_app_state:
        app_state = get_app_state()
        remote_session_url = app_state.get("remoteSessionUrl")
        if remote_session_url:
            lines.extend(
                [
                    "",
                    "**Remote session:**",
                    f"  {remote_session_url}",
                    "",
                    "(Use `/session` in interactive mode for QR code)",
                ]
            )
        else:
            lines.append("")
            lines.append("**Mode:** Local")
    else:
        lines.append("")
        lines.append("**Mode:** Local (not in remote mode)")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
