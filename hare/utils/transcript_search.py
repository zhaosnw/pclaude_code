"""Searchable text from transcript messages (port of transcriptSearch.ts)."""

from __future__ import annotations

from typing import Any

# WeakMap equivalent: id(msg) -> text cache
_search_cache: dict[int, str] = {}


def renderable_search_text(msg: Any) -> str:
    i = id(msg)
    if i in _search_cache:
        return _search_cache[i]
    raw = _compute_search_text(msg).lower()
    _search_cache[i] = raw
    return raw


def _compute_search_text(msg: Any) -> str:
    mtype = getattr(msg, "type", None)
    if mtype == "user":
        c = msg.message.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts: list[str] = []
            for b in c:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(str(b.get("text", "")))
            return "\n".join(parts)
    if mtype == "assistant":
        c = msg.message.content
        if isinstance(c, list):
            parts = []
            for b in c:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        parts.append(str(b.get("text", "")))
                    elif b.get("type") == "tool_use":
                        parts.append(str(b.get("input", "")))
            return "\n".join(parts)
    return ""
