"""Port of: src/utils/toolSearch.ts"""

from __future__ import annotations
from typing import Any


def is_tool_search_enabled() -> bool:
    return True


def extract_discovered_tool_names(messages: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for m in messages:
        c = m.get("message", {}).get("content", [])
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    names.add(b.get("name", ""))
    return names
