"""
Generate session title for /rename.

Port of: src/commands/rename/generateSessionName.ts
"""

from __future__ import annotations


async def generate_session_name_from_messages(
    _messages: list[dict[str, object]],
) -> str:
    return "Untitled session"
