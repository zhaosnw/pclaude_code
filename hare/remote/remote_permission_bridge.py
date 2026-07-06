"""
Remote permission bridge – build synthetic messages for permission UI.

Port of: src/remote/remotePermissionBridge.ts
"""

from __future__ import annotations
import uuid
from typing import Any


def create_synthetic_assistant_message(
    tool_name: str, tool_input: dict[str, Any]
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": str(uuid.uuid4()),
                "name": tool_name,
                "input": tool_input,
            }
        ],
    }


def create_tool_stub(name: str, description: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": {}},
    }
