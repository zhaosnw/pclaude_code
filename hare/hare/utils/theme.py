"""Terminal theme color map (`theme.ts` — partial)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Theme:
    auto_accept: str = ""
    bash_border: str = ""
    hare: str = ""
    text: str = ""
    background: str = ""
    success: str = ""
    error: str = ""
    warning: str = ""
    cyan_for_subagents_only: str = ""
    purple_for_subagents_only: str = ""


def get_default_theme() -> Theme:
    return Theme()
