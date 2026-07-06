"""
CronCreateTool – schedule a recurring or one-shot prompt.

Port of: src/tools/ScheduleCronTool/CronCreateTool.ts
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext
from hare.tools_impl.ScheduleCronTool.prompt import (
    CRON_CREATE_TOOL_NAME,
    DEFAULT_MAX_AGE_DAYS,
    build_cron_create_description,
    build_cron_create_prompt,
    is_durable_cron_enabled,
)

MAX_JOBS = 50

_cron_tasks: list[dict[str, Any]] = []


class _CronCreateTool(ToolBase):
    name = CRON_CREATE_TOOL_NAME
    aliases: list[str] = []
    search_hint = "schedule a recurring or one-shot prompt"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "cron": {
                    "type": "string",
                    "description": 'Standard 5-field cron expression: "M H DoM Mon DoW"',
                },
                "prompt": {
                    "type": "string",
                    "description": "The prompt to enqueue at each fire time.",
                },
                "recurring": {
                    "type": "boolean",
                    "description": f"true (default) = recurring until deleted or auto-expired after {DEFAULT_MAX_AGE_DAYS} days. false = one-shot.",
                },
                "durable": {
                    "type": "boolean",
                    "description": "true = persist to disk and survive restarts. false (default) = session-only.",
                },
            },
            "required": ["cron", "prompt"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def prompt(self, options: dict[str, Any]) -> str:
        return build_cron_create_prompt(is_durable_cron_enabled())

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return build_cron_create_description(is_durable_cron_enabled())

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return CRON_CREATE_TOOL_NAME

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        cron_expr = args.get("cron", "")
        prompt_text = args.get("prompt", "")
        recurring = args.get("recurring", True)
        durable = args.get("durable", False)

        if len(_cron_tasks) >= MAX_JOBS:
            return ToolResult(
                data=f"Too many scheduled jobs (max {MAX_JOBS}). Cancel one first.",
                is_error=True,
            )

        effective_durable = durable and is_durable_cron_enabled()
        job_id = f"cron-{uuid.uuid4().hex[:8]}"

        _cron_tasks.append(
            {
                "id": job_id,
                "cron": cron_expr,
                "prompt": prompt_text,
                "recurring": recurring,
                "durable": effective_durable,
            }
        )

        where = (
            "Persisted to .hare/scheduled_tasks.json"
            if effective_durable
            else "Session-only (not written to disk, dies when Hare exits)"
        )
        if recurring:
            msg = f"Scheduled recurring job {job_id} ({cron_expr}). {where}. Auto-expires after {DEFAULT_MAX_AGE_DAYS} days."
        else:
            msg = f"Scheduled one-shot task {job_id} ({cron_expr}). {where}. It will fire once then auto-delete."

        return ToolResult(data=msg)


CronCreateTool = _CronCreateTool()
