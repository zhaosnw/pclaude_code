"""
Tips – contextual tips for users.

Port of: src/services/tips/
"""

from __future__ import annotations
import random

_TIPS = [
    "Use /compact to reduce conversation size when it gets long.",
    "Press Escape to interrupt Hare at any time.",
    "Use @file to reference specific files in your prompt.",
    "Hare remembers context from HARE.md files in your project.",
    "Use /diff to see what changed in your session.",
    "Use /cost to track API usage costs.",
    "Press Tab for autocompletion of file paths and commands.",
    "Use /memory to view and edit session memory.",
    "Use Shift+Enter for multi-line input.",
    "Hare can read images – just reference the file path.",
]


def get_tip() -> str:
    return random.choice(_TIPS)


def get_all_tips() -> list[str]:
    return list(_TIPS)
