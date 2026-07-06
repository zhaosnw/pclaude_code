"""
Testing tools – never enabled in production.

Port of: src/tools/testing/TestingPermissionTool.tsx
"""

from __future__ import annotations

from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext

TESTING_PERMISSION_TOOL_NAME = "TestingPermission"


class _TestingPermissionTool(ToolBase):
    name = TESTING_PERMISSION_TOOL_NAME
    aliases: list[str] = []
    search_hint = "testing permission tool"
    max_result_size_chars = 100_000

    def is_enabled(self) -> bool:
        return False  # Never enabled in production

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def prompt(self, options: dict[str, Any]) -> str:
        return "Testing tool that always asks for permission."

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return "Testing permission tool"

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return TESTING_PERMISSION_TOOL_NAME

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        return ToolResult(data=f"Executed: {args.get('command', '')}")


TestingPermissionTool = _TestingPermissionTool()
