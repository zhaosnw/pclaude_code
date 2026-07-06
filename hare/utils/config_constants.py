"""Dependency-free config enumerations (mirrors `configConstants.ts`)."""

from __future__ import annotations

from typing import Final, Literal

NOTIFICATION_CHANNELS: Final[tuple[str, ...]] = (
    "auto",
    "iterm2",
    "iterm2_with_bell",
    "terminal_bell",
    "kitty",
    "ghostty",
    "notifications_disabled",
)

EDITOR_MODES: Final[tuple[str, ...]] = ("normal", "vim")

TEAMMATE_MODES: Final[tuple[str, ...]] = ("auto", "tmux", "in-process")

NotificationChannel = Literal[
    "auto",
    "iterm2",
    "iterm2_with_bell",
    "terminal_bell",
    "kitty",
    "ghostty",
    "notifications_disabled",
]
EditorMode = Literal["normal", "vim"]
TeammateMode = Literal["auto", "tmux", "in-process"]
