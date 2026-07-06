"""
System init messages.

Port of: src/utils/messages/systemInit.ts
"""

from __future__ import annotations

from typing import Any


def build_system_init_messages(
    system_prompt: str,
    git_status: str | None = None,
    session_memory: str | None = None,
) -> list[dict[str, Any]]:
    """Build initial system messages for a session."""
    messages: list[dict[str, Any]] = []
    if git_status:
        messages.append(
            {
                "type": "system",
                "content": git_status,
                "level": "info",
            }
        )
    if session_memory:
        messages.append(
            {
                "type": "system",
                "content": session_memory,
                "level": "info",
            }
        )
    return messages
