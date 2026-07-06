"""
SyntheticOutputTool – structured output tool (Ajv-compiled schema).

Port of: src/tools/SyntheticOutputTool/SyntheticOutputTool.ts
"""

from __future__ import annotations
import json
from typing import Any

TOOL_NAME = "SyntheticOutput"


def input_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


def is_synthetic_output_tool_enabled() -> bool:
    import os

    return bool(os.environ.get("SYNTHETIC_OUTPUT_ENABLED"))


async def call(input_data: Any = None, **kwargs: Any) -> dict[str, Any]:
    data_str = json.dumps(input_data) if input_data is not None else ""
    return {"data": data_str, "structured_output": input_data}


def create_synthetic_output_tool(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "input_schema": schema,
        "call": call,
    }
