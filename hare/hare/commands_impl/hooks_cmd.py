"""
/hooks command - view and manage hooks configuration.

Port of: src/commands/hooks/hooks.tsx + index.ts

Shows configured hooks with their event types, matchers, and commands.
In the TS CLI this renders a HooksConfigMenu UI component.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "hooks"
DESCRIPTION = "View and manage hooks configuration"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """List configured hooks or show management options."""
    get_app_state = context.get("get_app_state")
    get_hooks_config = context.get("get_hooks_config")
    hooks_dir = context.get("hooks_dir")

    if get_app_state:
        app_state = get_app_state()
    else:
        app_state = {}

    permission_context = app_state.get("toolPermissionContext", {})

    # Collect tool names
    get_tools = context.get("get_tools")
    tool_names = []
    if get_tools:
        tools = get_tools(permission_context)
        tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]

    # Get hooks config
    hooks = []
    if get_hooks_config:
        hooks = get_hooks_config()

    if not hooks:
        return {
            "type": "text",
            "value": "No hooks configured.\n\nUse CLAUDE.md hooks configuration or settings.json to add hooks.",
        }

    lines = ["## Configured Hooks", ""]
    for hook in hooks:
        event = hook.get("event", "unknown")
        matcher = hook.get("matcher", "*")
        command = hook.get("command", "")
        lines.append(f"- **{event}**: `{matcher}` → `{command}`")

    lines.extend(
        [
            "",
            f"**Available tool names for matchers:** {', '.join(f'`{n}`' for n in tool_names) if tool_names else 'none'}",
            "",
            "Configure hooks in `.claude/settings.json` or `~/.claude/settings.json`.",
        ]
    )

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
