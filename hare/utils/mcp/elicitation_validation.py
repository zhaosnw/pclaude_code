"""Port of: src/utils/mcp/elicitationValidation.ts"""

from __future__ import annotations
from typing import Any


def validate_elicitation_schema(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        errors.append("Schema must be an object")
        return errors
    if schema.get("type") != "object":
        errors.append("Schema type must be 'object'")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        errors.append("Properties must be an object")
    return errors
