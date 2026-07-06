"""
Keybinding definitions.

Port of: src/keybindings/keybindings.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Keybinding:
    key: str
    action: str
    description: str = ""
    modifier: str | None = None


DEFAULT_KEYBINDINGS: list[Keybinding] = [
    Keybinding(key="Enter", action="submit", description="Submit message"),
    Keybinding(key="Escape", action="cancel", description="Cancel current operation"),
    Keybinding(key="Tab", action="autocomplete", description="Autocomplete"),
    Keybinding(key="Up", action="history_prev", description="Previous history"),
    Keybinding(key="Down", action="history_next", description="Next history"),
    Keybinding(key="c", action="interrupt", modifier="ctrl", description="Interrupt"),
    Keybinding(key="d", action="exit", modifier="ctrl", description="Exit"),
    Keybinding(key="l", action="clear", modifier="ctrl", description="Clear screen"),
    Keybinding(
        key="r", action="search_history", modifier="ctrl", description="Search history"
    ),
    Keybinding(key="/", action="command", description="Enter command mode"),
]


def get_keybinding(action: str) -> Keybinding | None:
    for kb in DEFAULT_KEYBINDINGS:
        if kb.action == action:
            return kb
    return None
