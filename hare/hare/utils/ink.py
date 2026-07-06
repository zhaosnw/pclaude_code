"""
Ink theme color mapping for agent colors. Port of: src/utils/ink.ts
"""

from __future__ import annotations

# Mirrors AgentColorManager — stub theme keys for non-Ant builds
_DEFAULT_AGENT_THEME_COLOR = "cyan_FOR_SUBAGENTS_ONLY"

_AGENT_COLOR_TO_THEME_COLOR: dict[str, str] = {
    "blue": "blue_FOR_SUBAGENTS_ONLY",
    "green": "green_FOR_SUBAGENTS_ONLY",
    "red": "red_FOR_SUBAGENTS_ONLY",
    "yellow": "yellow_FOR_SUBAGENTS_ONLY",
    "magenta": "magenta_FOR_SUBAGENTS_ONLY",
    "cyan": "cyan_FOR_SUBAGENTS_ONLY",
}


def to_ink_color(color: str | None) -> str:
    """Map agent color string to Ink TextProps['color'] theme key or ansi: fallback."""
    if not color:
        return _DEFAULT_AGENT_THEME_COLOR
    theme = _AGENT_COLOR_TO_THEME_COLOR.get(color.lower())
    if theme:
        return theme
    return f"ansi:{color}"
