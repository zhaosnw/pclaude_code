"""Layout positions for swarm teammate UI. Port of: teammateLayoutManager.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TeammateLayout:
    teammate_id: str
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0


class TeammateLayoutManager:
    def __init__(self) -> None:
        self._layouts: dict[str, TeammateLayout] = {}

    def set_layout(self, layout: TeammateLayout) -> None:
        self._layouts[layout.teammate_id] = layout

    def get_layout(self, teammate_id: str) -> TeammateLayout | None:
        return self._layouts.get(teammate_id)
