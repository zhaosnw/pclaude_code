"""
/vim command - toggle vim keybindings mode.

Port of: src/commands/vim/ (2 files)

Toggles vim-style keybindings for the input area.
Persists the setting to app state.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "vim"
DESCRIPTION = "Toggle vim keybindings"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Toggle vim mode."""
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")

    current = False
    if get_app_state:
        app_state = get_app_state()
        current = app_state.get("vimMode", False)

    new_state = not current

    if set_app_state:

        def _toggle(prev: dict[str, Any]) -> dict[str, Any]:
            return {**prev, "vimMode": new_state}

        set_app_state(_toggle)

    status = "enabled" if new_state else "disabled"
    return {"type": "text", "value": f"Vim mode {status}."}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
