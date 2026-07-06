"""
Schema validator – validates tool input/output schemas.

Port of: src/services/schemaValidator/schemaValidator.ts
"""

from __future__ import annotations

from typing import Any


class SchemaError(Exception):
    def __init__(self, message: str, path: str = ""):
        super().__init__(message)
        self.path = path


def validate_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    """Validate data against a JSON schema. Returns list of error messages."""
    errors: list[str] = []
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(data, dict):
            errors.append(f"Expected object, got {type(data).__name__}")
            return errors
        required = schema.get("required", [])
        for r in required:
            if r not in data:
                errors.append(f"Missing required field: {r}")
        props = schema.get("properties", {})
        for key, prop_schema in props.items():
            if key in data:
                sub_errors = validate_schema(data[key], prop_schema)
                errors.extend(f"{key}.{e}" for e in sub_errors)
    elif schema_type == "string":
        if not isinstance(data, str):
            errors.append(f"Expected string, got {type(data).__name__}")
    elif schema_type == "number":
        if not isinstance(data, (int, float)):
            errors.append(f"Expected number, got {type(data).__name__}")
    elif schema_type == "boolean":
        if not isinstance(data, bool):
            errors.append(f"Expected boolean, got {type(data).__name__}")
    elif schema_type == "array":
        if not isinstance(data, list):
            errors.append(f"Expected array, got {type(data).__name__}")
    return errors
