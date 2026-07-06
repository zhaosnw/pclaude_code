"""
Buddy system – contextual assistant hints.

Port of: src/buddy/
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BuddyHint:
    id: str
    message: str
    condition: str = ""
    priority: int = 0


_HINTS: list[BuddyHint] = [
    BuddyHint(
        id="welcome",
        message="Welcome to Hare! Type your request or /help for commands.",
    ),
    BuddyHint(
        id="large_file",
        message="This file is large. Consider using offset/limit parameters.",
    ),
    BuddyHint(
        id="permission_denied",
        message="Permission denied. You may need to approve this tool.",
    ),
    BuddyHint(
        id="rate_limited", message="You've been rate limited. Waiting before retrying."
    ),
]


@dataclass
class BuddySystem:
    shown_hints: set[str] = field(default_factory=set)

    def get_hint(self, context: str = "") -> Optional[BuddyHint]:
        for h in _HINTS:
            if h.id not in self.shown_hints:
                if not h.condition or h.condition in context:
                    self.shown_hints.add(h.id)
                    return h
        return None

    def reset(self) -> None:
        self.shown_hints.clear()


def get_buddy_hint(context: str = "") -> Optional[str]:
    system = BuddySystem()
    hint = system.get_hint(context)
    return hint.message if hint else None
