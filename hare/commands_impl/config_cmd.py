"""Port of: src/commands/config/. View and manage configuration."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "config"
DESCRIPTION = "View and manage configuration settings"
ALIASES: list[str] = []


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show or modify configuration."""
    subcommand = args[0].lower() if args else "list"

    if subcommand == "list" or subcommand == "show":
        return await _list_config(args[1:], context)
    elif subcommand == "set":
        return await _set_config(args[1:], context)
    elif subcommand == "get":
        return await _get_config(args[1:], context)
    else:
        return {"type": "text", "value": "Usage: /config [list|get <key>|set <key> <value>]"}


async def _list_config(args: list[str], context: Any) -> dict[str, Any]:
    """List current configuration."""
    settings = {}
    ctx = context if isinstance(context, dict) else {}
    settings_getter = ctx.get("get_settings")
    if settings_getter:
        try:
            settings = settings_getter() if callable(settings_getter) else settings_getter
        except Exception:
            pass

    if not settings:
        return {"type": "text", "value": "No configuration found.\n\nSettings are stored in ~/.claude/settings.json"}

    lines = ["# Claude Code Configuration\n"]
    for key, value in sorted(settings.items()):
        if isinstance(value, (dict, list)):
            import json
            lines.append(f"**{key}**: `{json.dumps(value)[:120]}`")
        else:
            lines.append(f"- **{key}**: `{value}`")

    return {"type": "text", "value": "\n".join(lines)}


async def _get_config(args: list[str], context: Any) -> dict[str, Any]:
    key = args[0] if args else ""
    if not key:
        return {"type": "text", "value": "Usage: /config get <key>"}
    ctx = context if isinstance(context, dict) else {}
    settings_getter = ctx.get("get_settings")
    settings = {}
    if settings_getter:
        try:
            settings = settings_getter() if callable(settings_getter) else settings_getter
        except Exception:
            pass
    value = settings.get(key, "NOT SET")
    return {"type": "text", "value": f"{key} = {value}"}


async def _set_config(args: list[str], context: Any) -> dict[str, Any]:
    if len(args) < 2:
        return {"type": "text", "value": "Usage: /config set <key> <value>"}
    key = args[0]
    value = " ".join(args[1:])
    ctx = context if isinstance(context, dict) else {}
    settings_setter = ctx.get("set_settings")
    if settings_setter:
        try:
            if callable(settings_setter):
                await settings_setter(key, value)
                return {"type": "text", "value": f"Set {key} = {value}"}
        except Exception as e:
            return {"type": "text", "value": f"Error: {e}"}
    return {"type": "text", "value": f"Would set {key} = {value} (no config backend)"}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argument_hint": "[list|get <key>|set <key> <value>]",
    }
