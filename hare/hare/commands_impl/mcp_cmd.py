"""Port of: src/commands/mcp/. Manage MCP server configurations."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "mcp"
DESCRIPTION = "Manage MCP server configurations"
ALIASES: list[str] = []


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Manage MCP server configurations."""
    subcommand = args[0].lower() if args else "list"

    if subcommand in ("list", "ls", "show"):
        return await _mcp_list(args[1:], context)
    elif subcommand == "add":
        return await _mcp_add(args[1:], context)
    elif subcommand == "remove":
        return await _mcp_remove(args[1:], context)
    elif subcommand == "get":
        return await _mcp_get(args[1:], context)
    else:
        return {
            "type": "text",
            "value": (
                "Usage: /mcp [list|add <name> <command>|remove <name>|get <name>]\n\n"
                "MCP servers connect Claude Code to external tools and data sources.\n"
                "Configure in .mcp.json (project) or ~/.claude/mcp.json (user)."
            ),
        }


async def _mcp_list(args: list[str], context: Any) -> dict[str, Any]:
    ctx = context if isinstance(context, dict) else {}
    getter = ctx.get("get_mcp_configs")
    configs = {}
    if getter:
        try:
            configs = getter() if callable(getter) else getter
        except Exception:
            pass

    if not configs:
        return {
            "type": "text",
            "value": "No MCP servers configured.\n\nAdd one with: /mcp add <name> <command>"
        }

    lines = ["# Configured MCP Servers\n"]
    for name, cfg in sorted(configs.items()):
        if isinstance(cfg, dict):
            command = cfg.get("command", cfg.get("args", "unknown"))
            transport = cfg.get("transport", "stdio")
            enabled = cfg.get("enabled", True)
            status = "✓" if enabled else "✗"
            lines.append(f"  {status} **{name}**: `{command}` (transport: {transport})")
        else:
            lines.append(f"  - **{name}**: `{cfg}`")
    return {"type": "text", "value": "\n".join(lines)}


async def _mcp_add(args: list[str], context: Any) -> dict[str, Any]:
    if len(args) < 2:
        return {"type": "text", "value": "Usage: /mcp add <name> <command> [args...]"}
    name = args[0]
    command = " ".join(args[1:])
    return {
        "type": "text",
        "value": (
            f"# Adding MCP server: {name}\n\n"
            f"Add to `.mcp.json`:\n"
            f'```json\n'
            f'{{"mcpServers": {{\n'
            f'  "{name}": {{\n'
            f'    "command": "{command}",\n'
            f'    "args": []\n'
            f'  }}\n'
            f'}}}}\n'
            f'```\n'
            f"Then run /reload-plugins to connect."
        ),
    }


async def _mcp_remove(args: list[str], context: Any) -> dict[str, Any]:
    name = args[0] if args else ""
    if not name:
        return {"type": "text", "value": "Usage: /mcp remove <name>"}
    return {"type": "text", "value": f"Remove '{name}' from your .mcp.json file."}


async def _mcp_get(args: list[str], context: Any) -> dict[str, Any]:
    name = args[0] if args else ""
    if not name:
        return {"type": "text", "value": "Usage: /mcp get <name>"}
    ctx = context if isinstance(context, dict) else {}
    getter = ctx.get("get_mcp_configs")
    configs = {}
    if getter:
        try:
            configs = getter() if callable(getter) else getter
        except Exception:
            pass
    cfg = configs.get(name) if isinstance(configs, dict) else None
    if cfg:
        import json
        return {"type": "text", "value": f"# {name}\n```json\n{json.dumps(cfg, indent=2)}\n```"}
    return {"type": "text", "value": f"MCP server '{name}' not found."}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argument_hint": "[list|add <name> <command>|remove <name>|get <name>]",
    }
