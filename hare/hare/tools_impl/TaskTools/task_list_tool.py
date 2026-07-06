"""Task List Tool — list all background tasks and their status.

Port of: src/tools/TaskListTool/TaskListTool.ts
"""

from __future__ import annotations

from typing import Any

from hare.tools_impl.TaskTools.task_create_tool import get_all_tasks

TOOL_NAME = "TaskList"
DESCRIPTION = "List all background tasks and their status"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "status_filter": {
                "type": "string",
                "description": "Filter by status: pending, running, completed, failed, cancelled",
            },
        },
    }


async def call(status_filter: str = "", **kwargs: Any) -> dict[str, Any]:
    """List all background tasks."""
    all_tasks = get_all_tasks()

    if status_filter:
        all_tasks = [t for t in all_tasks if t.status == status_filter]

    task_list = []
    for t in sorted(all_tasks, key=lambda x: x.created_at, reverse=True):
        status_icon = {
            "pending": "○",
            "running": "⏳",
            "completed": "✓",
            "failed": "✗",
            "cancelled": "⊘",
        }.get(t.status, "?")
        task_list.append({
            "task_id": t.task_id,
            "description": t.description,
            "status": t.status,
            "status_icon": status_icon,
            "model": t.model or "default",
            "created_at": t.created_at,
        })

    return {"tasks": task_list, "count": len(task_list)}
