"""Port of: src/services/SessionMemory/sessionMemoryUtils.ts + prompts.ts"""

from __future__ import annotations
import os
from typing import Optional


def get_session_memory_content(memory_path: str = "") -> str:
    path = memory_path or os.path.join(os.path.expanduser("~"), ".hare", "HARE.md")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def is_session_memory_empty(memory_path: str = "") -> bool:
    content = get_session_memory_content(memory_path)
    return not content.strip()


def truncate_session_memory_for_compact(content: str, max_tokens: int = 5000) -> str:
    lines = content.split("\n")
    result = []
    est_tokens = 0
    for line in lines:
        line_tokens = len(line.split()) + 1
        if est_tokens + line_tokens > max_tokens:
            result.append("... [truncated for compaction]")
            break
        result.append(line)
        est_tokens += line_tokens
    return "\n".join(result)


def get_last_summarized_message_id() -> Optional[str]:
    return None


async def wait_for_session_memory_extraction() -> None:
    pass
