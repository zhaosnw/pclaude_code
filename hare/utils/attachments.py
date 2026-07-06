"""Port of: src/utils/attachments.ts"""

from __future__ import annotations
import os
from typing import Any


def create_attachment_message(attachments: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "attachment", "attachments": attachments}


def generate_file_attachment(file_path: str, cwd: str = "") -> dict[str, Any]:
    full = (
        os.path.join(cwd, file_path)
        if cwd and not os.path.isabs(file_path)
        else file_path
    )
    if not os.path.exists(full):
        return {"type": "file", "path": file_path, "error": "not found"}
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"type": "file", "path": file_path, "content": content[:50000]}
    except Exception as e:
        return {"type": "file", "path": file_path, "error": str(e)}


async def start_relevant_memory_prefetch(*args: Any, **kwargs: Any) -> None:
    """Prefetch relevant memories for session (P2 — stub)."""
    pass


def suppress_next_skill_listing() -> None:
    """Suppress the next skill listing (P2 — stub)."""
    pass


MEMORY_HEADER = "# Session Memory"
