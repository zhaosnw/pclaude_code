"""
Built-in Plugin Registry.

Port of: src/plugins/builtinPlugins.ts (159 lines)

Manages built-in plugins that ship with the CLI:
- Appear in /plugin UI under "Built-in" section
- User-toggleable enable/disable (persisted to settings)
- Can provide skills, hooks, MCP servers

Plugin IDs: `{name}@builtin` (vs marketplace `{name}@{marketplace}`).
"""

from __future__ import annotations

from typing import Any

BUILTIN_MARKETPLACE_NAME = "builtin"

_BUILTIN_PLUGINS: dict[str, dict[str, Any]] = {}


def register_builtin_plugin(definition: dict[str, Any]) -> None:
    """Register a built-in plugin at startup."""
    _BUILTIN_PLUGINS[definition["name"]] = definition


def is_builtin_plugin_id(plugin_id: str) -> bool:
    """Check if a plugin ID is built-in (ends with @builtin)."""
    return plugin_id.endswith(f"@{BUILTIN_MARKETPLACE_NAME}")


def get_builtin_plugin_definition(name: str) -> dict[str, Any] | None:
    """Get a specific built-in plugin definition by name."""
    return _BUILTIN_PLUGINS.get(name)


def get_builtin_plugins(get_settings: Any = None) -> dict[str, list[dict[str, Any]]]:
    """Get all registered built-in plugins, split into enabled/disabled.

    Plugins whose isAvailable() returns False are omitted.
    """
    settings = get_settings() if get_settings else {}
    enabled: list[dict[str, Any]] = []
    disabled: list[dict[str, Any]] = []

    for name, definition in _BUILTIN_PLUGINS.items():
        # Check availability
        is_available = definition.get("isAvailable")
        if is_available and not is_available():
            continue

        plugin_id = f"{name}@{BUILTIN_MARKETPLACE_NAME}"
        user_setting = None
        if "enabledPlugins" in settings:
            user_setting = settings["enabledPlugins"].get(plugin_id)

        # Enabled state: user preference > plugin default > True
        if user_setting is not None:
            is_enabled = user_setting is True
        else:
            is_enabled = definition.get("defaultEnabled", True)

        plugin: dict[str, Any] = {
            "name": name,
            "manifest": {
                "name": name,
                "description": definition.get("description", ""),
                "version": definition.get("version", ""),
            },
            "path": BUILTIN_MARKETPLACE_NAME,
            "source": plugin_id,
            "repository": plugin_id,
            "enabled": is_enabled,
            "isBuiltin": True,
            "hooksConfig": definition.get("hooks"),
            "mcpServers": definition.get("mcpServers"),
        }

        if is_enabled:
            enabled.append(plugin)
        else:
            disabled.append(plugin)

    return {"enabled": enabled, "disabled": disabled}


def get_builtin_plugin_skill_commands(get_settings: Any = None) -> list[dict[str, Any]]:
    """Get skills from enabled built-in plugins as command dicts."""
    result = get_builtin_plugins(get_settings)
    commands: list[dict[str, Any]] = []

    for plugin in result["enabled"]:
        definition = _BUILTIN_PLUGINS.get(plugin["name"])
        if not definition or not definition.get("skills"):
            continue
        for skill in definition["skills"]:
            commands.append(_skill_definition_to_command(skill))

    return commands


def clear_builtin_plugins() -> None:
    """Clear registry (for testing)."""
    _BUILTIN_PLUGINS.clear()


def _skill_definition_to_command(definition: dict[str, Any]) -> dict[str, Any]:
    """Convert a BundledSkillDefinition to a Command dict."""
    return {
        "type": "prompt",
        "name": definition.get("name", ""),
        "description": definition.get("description", ""),
        "hasUserSpecifiedDescription": True,
        "allowedTools": definition.get("allowedTools", []),
        "argumentHint": definition.get("argumentHint"),
        "whenToUse": definition.get("whenToUse"),
        "model": definition.get("model"),
        "disableModelInvocation": definition.get("disableModelInvocation", False),
        "userInvocable": definition.get("userInvocable", True),
        "contentLength": 0,
        "source": "bundled",
        "loadedFrom": "bundled",
        "hooks": definition.get("hooks"),
        "context": definition.get("context"),
        "agent": definition.get("agent"),
        "isEnabled": definition.get("isEnabled", lambda: True),
        "isHidden": not definition.get("userInvocable", True),
        "progressMessage": "running",
        "getPromptForCommand": definition.get("getPromptForCommand"),
    }
