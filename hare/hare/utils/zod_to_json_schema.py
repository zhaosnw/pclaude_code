"""Zod-like schema to JSON Schema (port of zodToJsonSchema.ts)."""

from __future__ import annotations

from typing import Any

# Cache by object identity for lazy schema wrappers (same as TS WeakMap)
_cache: dict[int, dict[str, Any]] = {}


def zod_to_json_schema(schema: Any) -> dict[str, Any]:
    """Convert a Zod v4 / Pydantic-compatible schema to JSON Schema."""
    key = id(schema)
    if key in _cache:
        return _cache[key]
    to_json = getattr(schema, "model_json_schema", None) or getattr(
        schema, "json_schema", None
    )
    if callable(to_json):
        result = to_json()
    else:
        to_js = getattr(schema, "to_json_schema", None)
        if callable(to_js):
            result = to_js()
        else:
            raise TypeError("schema must expose model_json_schema or to_json_schema")
    if not isinstance(result, dict):
        result = dict(result)  # type: ignore[arg-type]
    _cache[key] = result
    return result
