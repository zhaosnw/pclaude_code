"""
/voice command - toggle or configure voice input.

Port of: src/commands/voice/ (2 files)

Toggles voice input mode. In the TS CLI this integrates with microphone capture.
In the headless SDK, this is a stub that reports availability.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "voice"
DESCRIPTION = "Toggle voice input"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Toggle voice input mode."""
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")
    is_voice_available = context.get("is_voice_available")

    if is_voice_available:
        try:
            available = await is_voice_available()
            if not available:
                return {
                    "type": "text",
                    "value": "Voice input is not available. Microphone access is required.",
                    "display": "system",
                }
        except Exception:
            pass

    current = False
    if get_app_state:
        app_state = get_app_state()
        current = app_state.get("voiceEnabled", False)

    new_state = not current

    if set_app_state:

        def _toggle(prev: dict[str, Any]) -> dict[str, Any]:
            return {**prev, "voiceEnabled": new_state}

        set_app_state(_toggle)

    status = "enabled" if new_state else "disabled"
    return {"type": "text", "value": f"Voice input {status}."}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
