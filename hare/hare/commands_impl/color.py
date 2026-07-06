"""
/color command - set the session's agent color.

Port of: src/commands/color/color.ts + index.ts

Manages agent color with:
  - Color list validation against AGENT_COLORS
  - Reset aliases: default, reset, none, gray, grey
  - Teammate guard (teammates can't set their own color)
  - Persistence to transcript via saveAgentColor
  - Immediate app state update via standaloneAgentContext
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "color"
DESCRIPTION = "Change the agent color for this session"
ALIASES: list[str] = []

AGENT_COLORS = [
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
    "black",
    "gray",
    "grey",
    "orange",
    "pink",
    "purple",
    "teal",
    "lime",
    "maroon",
    "navy",
    "olive",
    "silver",
    "aqua",
    "fuchsia",
]

RESET_ALIASES = {"default", "reset", "none", "gray", "grey"}


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Set the agent color for the current session.

    Color is persisted to transcript and applied immediately.
    'default' or 'reset' aliases clear the color.
    """
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")
    get_session_id = context.get("get_session_id")
    get_transcript_path = context.get("get_transcript_path")
    save_agent_color = context.get("save_agent_color")
    is_teammate = context.get("is_teammate")

    # Teammates cannot set their own color
    if is_teammate and is_teammate():
        return {
            "type": "text",
            "value": "Cannot set color: This session is a swarm teammate. Teammate colors are assigned by the team leader.",
            "display": "system",
        }

    if not args or not args.strip():
        color_list = ", ".join(AGENT_COLORS)
        return {
            "type": "text",
            "value": f"Please provide a color. Available colors: {color_list}, default",
            "display": "system",
        }

    color_arg = args.strip().lower()

    # Handle reset to default
    if color_arg in RESET_ALIASES:
        session_id = get_session_id() if get_session_id else ""
        full_path = get_transcript_path() if get_transcript_path else ""

        if save_agent_color:
            await save_agent_color(session_id, "default", full_path)

        if set_app_state:

            def _reset_color(prev: dict[str, Any]) -> dict[str, Any]:
                sac = prev.get("standaloneAgentContext", {})
                return {
                    **prev,
                    "standaloneAgentContext": {
                        **sac,
                        "name": sac.get("name", ""),
                        "color": None,
                    },
                }

            set_app_state(_reset_color)

        return {
            "type": "text",
            "value": "Session color reset to default",
            "display": "system",
        }

    # Validate color
    if color_arg not in AGENT_COLORS:
        color_list = ", ".join(AGENT_COLORS)
        return {
            "type": "text",
            "value": f'Invalid color "{color_arg}". Available colors: {color_list}, default',
            "display": "system",
        }

    session_id = get_session_id() if get_session_id else ""
    full_path = get_transcript_path() if get_transcript_path else ""

    # Save to transcript for persistence across sessions
    if save_agent_color:
        await save_agent_color(session_id, color_arg, full_path)

    # Update AppState for immediate effect
    if set_app_state:

        def _set_color(prev: dict[str, Any]) -> dict[str, Any]:
            sac = prev.get("standaloneAgentContext", {})
            return {
                **prev,
                "standaloneAgentContext": {
                    **sac,
                    "name": sac.get("name", ""),
                    "color": color_arg,
                },
            }

        set_app_state(_set_color)

    return {
        "type": "text",
        "value": f"Session color set to: {color_arg}",
        "display": "system",
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "<color | default>",
        "call": call,
    }
