"""Keyboard shortcut registry for TUI. Port of: keyboardShortcuts.ts"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ShortcutMap:
    bindings: dict[str, Callable[[], None]] = field(default_factory=dict)

    def register(self, key: str, fn: Callable[[], None]) -> None:
        self.bindings[key] = fn
