"""
Boolean fields that accept JSON string "true"/"false". Port of src/utils/semanticBoolean.ts.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator


def _preprocess_bool(v: Any) -> Any:
    if v == "true":
        return True
    if v == "false":
        return False
    return v


def semantic_boolean(inner: type | None = None) -> Any:
    """
    Wrap a bool schema with client-side coercion from quoted booleans.
    Use inside Pydantic model fields: `field: semantic_boolean()` or
    `field: semantic_boolean(Optional[bool])` — pass inner type for optional/default.
    """
    base: Any = bool if inner is None else inner
    return Annotated[base, BeforeValidator(_preprocess_bool)]
