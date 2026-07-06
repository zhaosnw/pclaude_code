"""Port of: src/buddy/prompt.ts — companion intro attachment helpers."""

from __future__ import annotations

from typing import Any, Optional

from hare.utils.config import get_global_config

from .companion import get_companion


def _buddy_feature_enabled() -> bool:
    return True


def companion_intro_text(name: str, species: str) -> str:
    return f"""# Companion

A small {species} named {name} sits beside the user's input box and occasionally comments in a speech bubble. You're not {name} — it's a separate watcher.

When the user addresses {name} directly (by name), its bubble will answer. Your job in that moment is to stay out of the way: respond in ONE line or less, or just answer any part of the message meant for you. Don't explain that you're not {name} — they know. Don't narrate what {name} might say — the bubble handles that."""


def get_companion_intro_attachment(
    messages: Optional[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if not _buddy_feature_enabled():
        return []
    cfg = get_global_config()
    if getattr(cfg, "companion_muted", False):
        return []
    companion = get_companion()
    if not companion:
        return []
    for msg in messages or []:
        if msg.get("type") != "attachment":
            continue
        att = msg.get("attachment") or {}
        if att.get("type") != "companion_intro":
            continue
        if att.get("name") == companion.name:
            return []
    return [
        {
            "type": "companion_intro",
            "name": companion.name,
            "species": companion.species,
        }
    ]
