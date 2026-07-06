"""Terminal panel coordination for IDE-embedded UI. Port of: terminalPanel.ts"""

from __future__ import annotations


class TerminalPanel:
    def __init__(self, panel_id: str) -> None:
        self.panel_id = panel_id

    async def show(self) -> None:
        return

    async def hide(self) -> None:
        return
