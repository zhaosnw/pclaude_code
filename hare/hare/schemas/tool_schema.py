"""
Tool input schema definition.

Port of: src/schemas/toolSchema.ts
"""

from __future__ import annotations

from typing import Any

TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_name": {"type": "string"},
        "arguments": {"type": "object"},
    },
    "required": ["tool_name"],
}
