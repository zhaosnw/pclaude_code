"""
/plugin command - manage plugins (list, install, uninstall).

Port of: src/commands/plugin/ (16 files)

Manages plugins: lists installed, provides marketplace info,
parses install/uninstall/enable/disable subcommands.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "plugin"
DESCRIPTION = "Manage plugins (install, list, remove)"
ALIASES = ["plugins"]

SUBCOMMANDS = ["install", "uninstall", "list", "enable", "disable", "search", "update"]


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Manage plugins."""
    parts = (args or "").strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1] if len(parts) > 1 else ""

    load_plugins = context.get("load_plugins")
    install_plugin = context.get("install_plugin")
    uninstall_plugin = context.get("uninstall_plugin")
    search_marketplace = context.get("search_marketplace")

    if subcommand == "list" or not subcommand:
        plugins = []
        if load_plugins:
            plugins = load_plugins()

        if not plugins:
            return {
                "type": "text",
                "value": (
                    "No plugins installed.\n\n"
                    "Install plugins from the marketplace:\n"
                    "  `/plugin install <name>`\n"
                    "Search for plugins:\n"
                    "  `/plugin search <query>`"
                ),
            }

        lines = ["## Installed Plugins", ""]
        for p in plugins:
            name = p.get("name", "unknown")
            version = p.get("version", "")
            desc = p.get("description", "")
            enabled = p.get("enabled", True)
            status = "" if enabled else " (disabled)"
            v_str = f" v{version}" if version else ""
            lines.append(f"- **{name}**{v_str}{status}")
            if desc:
                lines.append(f"  {desc}")

        return {"type": "text", "value": "\n".join(lines)}

    elif subcommand == "install" and sub_args:
        if install_plugin:
            try:
                result = await install_plugin(sub_args)
                return {
                    "type": "text",
                    "value": f"Plugin installed: {result.get('name', sub_args)}",
                }
            except Exception as e:
                return {
                    "type": "text",
                    "value": f"Failed to install plugin '{sub_args}': {e}",
                    "display": "system",
                }
        return {
            "type": "text",
            "value": f"Plugin installation is not available in headless mode.\nTo install: `/plugin install {sub_args}`",
        }

    elif subcommand == "uninstall" and sub_args:
        if uninstall_plugin:
            try:
                await uninstall_plugin(sub_args)
                return {
                    "type": "text",
                    "value": f"Plugin uninstalled: {sub_args}",
                }
            except Exception as e:
                return {
                    "type": "text",
                    "value": f"Failed to uninstall plugin '{sub_args}': {e}",
                    "display": "system",
                }
        return {
            "type": "text",
            "value": "Plugin uninstallation is not available in headless mode.",
        }

    elif subcommand == "search" and sub_args:
        if search_marketplace:
            results = await search_marketplace(sub_args)
            if not results:
                return {
                    "type": "text",
                    "value": f"No plugins found matching '{sub_args}'.",
                }
            lines = [f"## Search Results for '{sub_args}'", ""]
            for r in results:
                lines.append(f"- **{r.get('name', '')}** — {r.get('description', '')}")
            return {"type": "text", "value": "\n".join(lines)}
        return {
            "type": "text",
            "value": "Plugin marketplace search is not available in headless mode.",
        }

    else:
        return {
            "type": "text",
            "value": (
                f"Usage: /plugin <command>\n\n"
                f"Commands: {', '.join(SUBCOMMANDS)}\n\n"
                f"Examples:\n"
                f"  /plugin list          — list installed plugins\n"
                f"  /plugin install <name> — install a plugin\n"
                f"  /plugin search <query> — search marketplace\n"
                f"  /plugin uninstall <name> — remove a plugin"
            ),
        }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[install|uninstall|list|search|enable|disable]",
        "call": call,
    }
