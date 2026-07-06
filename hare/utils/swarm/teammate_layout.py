"""
Teammate color/layout management.

Port of: src/utils/swarm/teammateLayoutManager.ts
"""

from __future__ import annotations

_COLORS = [
    "cyan",
    "magenta",
    "yellow",
    "green",
    "blue",
    "red",
    "white",
    "gray",
]

_assignments: dict[str, str] = {}
_next_idx = 0


def assign_teammate_color(agent_id: str) -> str:
    global _next_idx
    if agent_id in _assignments:
        return _assignments[agent_id]
    color = _COLORS[_next_idx % len(_COLORS)]
    _assignments[agent_id] = color
    _next_idx += 1
    return color


def clear_teammate_colors() -> None:
    global _next_idx
    _assignments.clear()
    _next_idx = 0
