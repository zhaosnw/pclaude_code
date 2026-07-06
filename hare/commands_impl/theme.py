"""
/theme command - change the terminal/display theme.

Port of: src/commands/theme/theme.tsx + index.ts

Lists available themes and applies a selected theme.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "theme"
DESCRIPTION = "Change the display theme"
ALIASES: list[str] = []

THEMES = ["dark", "light", "system", "monokai", "solarized"]

THEME_DESCRIPTIONS = {
    "dark": "Dark theme (light text on dark background)",
    "light": "Light theme (dark text on light background)",
    "system": "Follow system theme",
    "monokai": "Monokai color scheme",
    "solarized": "Solarized color scheme",
}


async def call(args: str, **context: Any) -> dict[str, Any]:
    """List or set the theme."""
    set_theme = context.get("set_theme")
    get_theme = context.get("get_theme")

    theme_name = (args or "").strip().lower()

    if not theme_name:
        current = get_theme() if get_theme else "dark"
        lines = ["## Available Themes", ""]
        for t in THEMES:
            marker = " (current)" if t == current else ""
            lines.append(f"- **{t}**{marker} — {THEME_DESCRIPTIONS.get(t, '')}")
        lines.extend(
            ["", f"Current theme: **{current}**", "", "Use `/theme <name>` to change."]
        )
        return {"type": "text", "value": "\n".join(lines)}

    if theme_name not in THEMES:
        return {
            "type": "text",
            "value": f'Unknown theme "{theme_name}". Available: {", ".join(THEMES)}',
            "display": "system",
        }

    if set_theme:
        set_theme(theme_name)

    return {"type": "text", "value": f"Theme set to: {theme_name}"}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[theme-name]",
        "call": call,
    }
