"""
/remote-control command — bridge session management.

Port of: src/commands/bridge/bridge.tsx (508 lines)

Remote control bridge — connect to claude.ai for remote sessions.
Shows bridge status, connection info, and session management.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "remote-control"
DESCRIPTION = "Remote control / bridge session management"
ALIASES = ["bridge", "rc"]


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show bridge status or manage remote control sessions."""
    get_app_state = context.get("get_app_state")
    is_bridge_enabled = context.get("is_bridge_enabled")
    get_bridge_status = context.get("get_bridge_status")
    get_bridge_connect_url = context.get("get_bridge_connect_url")

    arg = (args or "").strip().lower()

    # Check if bridge is available
    if is_bridge_enabled and not is_bridge_enabled():
        return {
            "type": "text",
            "value": (
                "## Remote Control\n\n"
                "Remote Control is not available for this session.\n\n"
                "Requirements:\n"
                "- claude.ai subscription\n"
                "- Logged in via `/login`\n"
                "- Supported CLI version\n\n"
                "Run `/login` to sign in first."
            ),
        }

    # Get bridge state
    app_state = get_app_state() if get_app_state else {}
    connected = (
        app_state.get("repl_bridge_connected", False)
        if isinstance(app_state, dict)
        else getattr(app_state, "repl_bridge_connected", False)
    )
    session_active = (
        app_state.get("repl_bridge_session_active", False)
        if isinstance(app_state, dict)
        else getattr(app_state, "repl_bridge_session_active", False)
    )
    env_id = (
        app_state.get("repl_bridge_environment_id", "")
        if isinstance(app_state, dict)
        else getattr(app_state, "repl_bridge_environment_id", "")
    )

    # Status action
    if arg == "status":
        status_label = "Connected" if connected else "Disconnected"
        lines = [
            "## Remote Control Status",
            "",
            f"**Connection:** {status_label}",
            f"**Session active:** {session_active}",
        ]
        if env_id:
            lines.append(f"**Environment:** `{env_id[:8]}...`")
        if get_bridge_connect_url:
            lines.append(f"**Connect URL:** {get_bridge_connect_url()}")

        if not connected:
            lines.extend(
                [
                    "",
                    "Start remote control:",
                    "```bash",
                    "claude remote-control",
                    "```",
                    "Or use `/remote-control start` in an interactive session.",
                ]
            )
        return {"type": "text", "value": "\n".join(lines)}

    # Start
    if arg in ("start", "connect", "on"):
        return {
            "type": "text",
            "value": (
                "## Start Remote Control\n\n"
                "To start remote control from command line:\n"
                "```bash\n"
                "claude remote-control\n"
                "```\n\n"
                "With a session name:\n"
                "```bash\n"
                'claude remote-control "My Session"\n'
                "```\n\n"
                "To reconnect to a previous session:\n"
                "```bash\n"
                "claude remote-control --session-id <id>\n"
                "```"
            ),
        }

    # Stop
    if arg in ("stop", "disconnect", "off"):
        return {
            "type": "text",
            "value": (
                "## Stop Remote Control\n\n"
                "Remote control will stop when the CLI exits.\n"
                "To stop immediately, press Ctrl+C or close the terminal.\n\n"
                "Connected sessions will be archived automatically."
            ),
        }

    # Default: show overview
    lines = [
        "## Remote Control",
        "",
        "Remote Control lets you use Claude Code from the web or mobile app.",
        "",
    ]
    if connected:
        lines.append("**Status:** Connected")
        if env_id:
            lines.append(f"**Environment:** `{env_id[:8]}...`")
        lines.append("")
        lines.append("Commands:")
        lines.append("- `/remote-control status` — view connection details")
        lines.append("- `/remote-control stop` — disconnect")
    else:
        lines.append("**Status:** Not connected")
        lines.append("")
        lines.append("Commands:")
        lines.append("- `/remote-control start` — start remote control")
        lines.append("- `/remote-control status` — check if available")

    if get_bridge_connect_url:
        lines.append("")
        lines.append(f"Connect at: {get_bridge_connect_url()}")

    return {"type": "text", "value": "\n".join(lines)}
