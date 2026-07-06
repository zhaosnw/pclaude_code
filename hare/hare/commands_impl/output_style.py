"""
/output-style command - set the AI output style.

Port of: src/commands/output-style/ (2 files)

Controls how the AI formats its responses:
  - concise: Brief, direct answers
  - explanatory: Detailed explanations
  - learning: Tutorial-style responses with context
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "output-style"
DESCRIPTION = "Set the AI output style (concise, explanatory, learning)"
ALIASES = ["style"]

OUTPUT_STYLES = {
    "concise": "Concise — brief, direct answers with minimal verbosity",
    "explanatory": "Explanatory — detailed explanations with reasoning",
    "learning": "Learning — tutorial-style responses with context and examples",
    "default": "Default — standard response style",
}


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Set or query the output style."""
    style = (args or "").strip().lower()
    set_app_state = context.get("set_app_state")
    get_app_state = context.get("get_app_state")

    if not style:
        current = "default"
        if get_app_state:
            app_state = get_app_state()
            current = app_state.get("outputStyle", "default")

        lines = ["## Available Output Styles", ""]
        for name, desc in OUTPUT_STYLES.items():
            marker = " (current)" if name == current else ""
            lines.append(f"- **{name}**{marker} — {desc}")
        lines.extend(
            [
                "",
                f"Current style: **{current}**",
                "",
                "Use `/output-style <name>` to change.",
            ]
        )
        return {"type": "text", "value": "\n".join(lines)}

    # Normalize: match the closest known style
    if style in OUTPUT_STYLES:
        resolved = style
    else:
        return {
            "type": "text",
            "value": f'Unknown style "{style}". Available: {", ".join(OUTPUT_STYLES.keys())}',
            "display": "system",
        }

    if set_app_state:

        def _set_style(prev: dict[str, Any]) -> dict[str, Any]:
            return {**prev, "outputStyle": resolved}

        set_app_state(_set_style)

    return {"type": "text", "value": f"Output style set to: {resolved}"}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[concise|explanatory|learning|default]",
        "call": call,
    }
