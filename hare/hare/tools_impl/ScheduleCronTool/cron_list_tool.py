"""
CronListTool – list active cron jobs.

Port of: src/tools/ScheduleCronTool/CronListTool.ts
"""

from __future__ import annotations

from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext
from hare.tools_impl.ScheduleCronTool.prompt import (
    CRON_LIST_TOOL_NAME,
    CRON_LIST_DESCRIPTION,
    build_cron_list_prompt,
    is_durable_cron_enabled,
)


class _CronListTool(ToolBase):
    name = CRON_LIST_TOOL_NAME
    aliases: list[str] = []
    search_hint = "list active cron jobs"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def prompt(self, options: dict[str, Any]) -> str:
        return build_cron_list_prompt(is_durable_cron_enabled())

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return CRON_LIST_DESCRIPTION

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return CRON_LIST_TOOL_NAME

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        from hare.tools_impl.ScheduleCronTool.cron_create_tool import _cron_tasks

        if not _cron_tasks:
            return ToolResult(data="No scheduled jobs.")

        lines: list[str] = []
        for t in _cron_tasks:
            recur = "(recurring)" if t.get("recurring") else "(one-shot)"
            durable_flag = " [session-only]" if t.get("durable") is False else ""
            prompt_text = t.get("prompt", "")
            if len(prompt_text) > 80:
                prompt_text = prompt_text[:77] + "..."
            lines.append(
                f"{t['id']} — {t['cron']} {recur}{durable_flag}: {prompt_text}"
            )

        return ToolResult(data="\n".join(lines))


CronListTool = _CronListTool()
