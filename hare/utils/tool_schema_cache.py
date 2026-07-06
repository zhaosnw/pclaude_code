"""Session-scoped tool schema cache (port of toolSchemaCache.ts)."""

from __future__ import annotations

from typing import Any

_TOOL_SCHEMA_CACHE: dict[str, Any] = {}


def get_tool_schema_cache() -> dict[str, Any]:
    return _TOOL_SCHEMA_CACHE


def clear_tool_schema_cache() -> None:
    _TOOL_SCHEMA_CACHE.clear()
