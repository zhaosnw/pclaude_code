"""
SDK tool types.

Port of: src/entrypoints/sdk/toolTypes.ts
"""

from __future__ import annotations

from typing import Any, Literal


ToolResultStatus = Literal["success", "error", "cancelled"]


class SDKToolResult:
    def __init__(
        self,
        tool_use_id: str = "",
        status: ToolResultStatus = "success",
        output: str = "",
        error: str | None = None,
    ) -> None:
        self.tool_use_id = tool_use_id
        self.status = status
        self.output = output
        self.error = error


class SDKToolInput:
    def __init__(
        self,
        tool_use_id: str = "",
        tool_name: str = "",
        input_args: dict[str, Any] | None = None,
    ) -> None:
        self.tool_use_id = tool_use_id
        self.tool_name = tool_name
        self.input_args = input_args or {}
