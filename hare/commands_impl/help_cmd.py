"""Port of: src/commands/help/. Show help for Claude Code commands."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "help"
DESCRIPTION = "Show help for Claude Code commands"
ALIASES: list[str] = ["h", "?"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """List all commands with their descriptions."""
    commands = []
    if isinstance(context, dict):
        commands = context.get("commands", [])
        get_commands_fn = context.get("get_commands")
        if get_commands_fn:
            try:
                cmds = await get_commands_fn() if callable(get_commands_fn) else get_commands_fn
                if isinstance(cmds, list):
                    commands = cmds
            except Exception:
                pass

    if not commands:
        return {
            "type": "text",
            "value": "Available commands: /help, /model, /config, /clear, /compact, /cost, "
                     "/diff, /status, /exit, /resume, /doctor, /doctor, /init, "
                     "/rename, /theme, /upgrade, /mcp, /permissions, /tasks, "
                     "/agent, /login, /logout, /hooks, /plugins, /release-notes, "
                     "/review, /security-review, /skills, /voice, /vim, /ide, "
                     "/terminal-setup, /add-dir, /export, /stats, /usage, "
                     "/pr-comments, /bug-hunter, /output-style, /effort, /fast.\n\n"
                     "Type /<command> --help for more details.\n"
                     "For more information: https://docs.anthropic.com/en/docs/claude-code",
        }

    lines = ["Available commands:", ""]
    for cmd in commands:
        if isinstance(cmd, dict):
            name = cmd.get("name", "")
            desc = cmd.get("description", "")
            lines.append(f"  /{name:<22} {desc}")
        elif hasattr(cmd, "name"):
            name = getattr(cmd, "name", "")
            desc = getattr(cmd, "description", "")
            lines.append(f"  /{name:<22} {desc}")

    lines.append("")
    lines.append("Type /<command> --help for more details.")
    lines.append("Docs: https://docs.anthropic.com/en/docs/claude-code")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argument_hint": "[command]",
    }
