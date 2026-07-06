"""
/status command - show current session and project status.

Port of: src/commands/status/status.tsx + index.ts

Shows session state: mode, working directories, model, permissions, etc.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "status"
DESCRIPTION = "Show current session and project status"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show session status."""
    get_app_state = context.get("get_app_state")
    get_session_id = context.get("get_session_id")
    options = context.get("options", {})

    session_id = get_session_id() if get_session_id else "unknown"
    model = options.get("mainLoopModel", "default")

    lines = [
        "## Status",
        "",
        f"**Session:** {session_id}",
        f"**Model:** {model}",
    ]

    if get_app_state:
        app_state = get_app_state()

        is_brief_only = app_state.get("isBriefOnly", False)
        sandbox_enabled = app_state.get("sandboxEnabled", False)
        vim_mode = app_state.get("vimMode", False)
        fast_mode = app_state.get("fastMode", False)

        lines.append(f"**Brief mode:** {'on' if is_brief_only else 'off'}")
        lines.append(f"**Sandbox:** {'on' if sandbox_enabled else 'off'}")
        lines.append(f"**Vim mode:** {'on' if vim_mode else 'off'}")
        lines.append(f"**Fast mode:** {'on' if fast_mode else 'off'}")

        # Working directories
        permission_context = app_state.get("toolPermissionContext", {})
        cwd = permission_context.get("cwd", ".")
        lines.append(f"**Working directory:** {cwd}")

        additional = permission_context.get("additionalWorkingDirectories", {})
        if additional:
            dirs = (
                list(additional.keys())
                if isinstance(additional, dict)
                else list(additional)
            )
            lines.append(f"**Additional directories:** {', '.join(dirs)}")

        # Permissions summary
        always_allow = permission_context.get("alwaysAllowRules", {})
        if always_allow:
            lines.append(f"**Always-allowed tools:** {len(always_allow)} rule(s)")

        # Tasks
        tasks = app_state.get("tasks", {})
        running_tasks = [
            t
            for t in tasks.values()
            if isinstance(t, dict) and t.get("status") == "running"
        ]
        if running_tasks:
            lines.append(f"**Background tasks:** {len(running_tasks)} running")

        # MCP
        mcp = app_state.get("mcp", {})
        mcp_clients = mcp.get("clients", [])
        if mcp_clients:
            lines.append(f"**MCP clients:** {len(mcp_clients)} connected")

    lines.append("")
    lines.append("**Active**")
    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
