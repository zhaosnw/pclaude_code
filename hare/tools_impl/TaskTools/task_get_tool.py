"""Task Get Tool — get details of a specific task.

Port of: src/tools/TaskGetTool/TaskGetTool.ts
"""

from __future__ import annotations

from typing import Any

from hare.tools_impl.TaskTools.task_create_tool import get_task

TOOL_NAME = "TaskGet"
DESCRIPTION = "Get details of a specific background task"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to look up",
            },
        },
        "required": ["task_id"],
    }


async def call(task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Get details for a specific task by ID."""
    task = get_task(task_id)
    if task is None:
        return {
            "task_id": task_id,
            "status": "not_found",
            "message": f"No task found with id '{task_id}'.",
        }

    return {
        "task_id": task.task_id,
        "description": task.description,
        "prompt": task.prompt,
        "status": task.status,
        "model": task.model,
        "error": task.error,
        "result": task.result,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }
