"""
/sandbox command - toggle or configure sandbox mode.

Port of: src/commands/sandbox-toggle/ (2 files)

Toggles sandbox mode which restricts bash command execution.
When enabled, file operations and network access are restricted.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "sandbox"
DESCRIPTION = "Toggle sandbox mode"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Toggle sandbox mode."""
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")
    sandbox_manager = context.get("sandbox_manager")

    current = False
    if get_app_state:
        app_state = get_app_state()
        current = app_state.get("sandboxEnabled", False)

    new_state = not current

    if set_app_state:

        def _toggle(prev: dict[str, Any]) -> dict[str, Any]:
            return {**prev, "sandboxEnabled": new_state}

        set_app_state(_toggle)

    if sandbox_manager and hasattr(sandbox_manager, "refresh_config"):
        sandbox_manager.refresh_config()

    status = "enabled" if new_state else "disabled"
    return {
        "type": "text",
        "value": f"Sandbox mode {status}.\n\nBash commands {'will' if new_state else 'will not'} be restricted.",
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
