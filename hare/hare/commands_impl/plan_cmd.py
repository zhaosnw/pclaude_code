"""
/plan command - toggle plan mode.

Port of: src/commands/plan/ (2 files)

Toggles plan mode: in plan mode, the AI makes a plan before implementing.
This toggles the appState.planMode flag.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "plan"
DESCRIPTION = "Toggle plan mode (plan before implementing)"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Toggle plan mode."""
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")

    current = False
    if get_app_state:
        app_state = get_app_state()
        current = app_state.get("planMode", False)

    new_state = not current

    if set_app_state:

        def _toggle(prev: dict[str, Any]) -> dict[str, Any]:
            return {**prev, "planMode": new_state}

        set_app_state(_toggle)

    status = "enabled" if new_state else "disabled"
    return {"type": "text", "value": f"Plan mode {status}."}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
