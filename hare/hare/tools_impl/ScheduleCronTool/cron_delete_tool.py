"""
CronDeleteTool – cancel a scheduled cron job.

Port of: src/tools/ScheduleCronTool/CronDeleteTool.ts
"""

from __future__ import annotations

from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext
from hare.tools_impl.ScheduleCronTool.prompt import (
    CRON_DELETE_TOOL_NAME,
    CRON_DELETE_DESCRIPTION,
    build_cron_delete_prompt,
    is_durable_cron_enabled,
)


class _CronDeleteTool(ToolBase):
    name = CRON_DELETE_TOOL_NAME
    aliases: list[str] = []
    search_hint = "cancel a scheduled cron job"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Job ID returned by CronCreate.",
                },
            },
            "required": ["id"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def prompt(self, options: dict[str, Any]) -> str:
        return build_cron_delete_prompt(is_durable_cron_enabled())

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return CRON_DELETE_DESCRIPTION

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return CRON_DELETE_TOOL_NAME

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        from hare.tools_impl.ScheduleCronTool.cron_create_tool import _cron_tasks

        job_id = args.get("id", "")
        idx = next((i for i, t in enumerate(_cron_tasks) if t["id"] == job_id), -1)
        if idx < 0:
            return ToolResult(
                data=f"No scheduled job with id '{job_id}'",
                is_error=True,
            )
        _cron_tasks.pop(idx)
        return ToolResult(data=f"Cancelled job {job_id}.")


CronDeleteTool = _CronDeleteTool()
