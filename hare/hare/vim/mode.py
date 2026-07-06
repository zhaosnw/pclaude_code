"""
Vim mode state management.

Port of: src/vim/vimMode.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VimMode = Literal["normal", "insert", "visual", "command"]


@dataclass
class VimState:
    mode: VimMode = "insert"
    command_buffer: str = ""
    register: str = ""
    count: int = 0

    def to_normal(self) -> None:
        self.mode = "normal"
        self.command_buffer = ""

    def to_insert(self) -> None:
        self.mode = "insert"
        self.command_buffer = ""

    def to_visual(self) -> None:
        self.mode = "visual"

    def to_command(self) -> None:
        self.mode = "command"
        self.command_buffer = ":"

    def feed_key(self, key: str) -> str | None:
        """Process a key press. Returns action string or None."""
        if self.mode == "normal":
            if key == "i":
                self.to_insert()
                return "insert"
            elif key == ":":
                self.to_command()
                return "command"
            elif key == "v":
                self.to_visual()
                return "visual"
        elif self.mode == "insert":
            if key == "Escape":
                self.to_normal()
                return "normal"
        elif self.mode == "command":
            if key == "Escape":
                self.to_normal()
                return "normal"
            elif key == "Enter":
                cmd = self.command_buffer
                self.to_normal()
                return f"exec:{cmd}"
            else:
                self.command_buffer += key
        return None
