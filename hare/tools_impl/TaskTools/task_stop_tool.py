"""Task Stop Tool — stop a running background task.

Port of: src/tools/TaskStopTool/TaskStopTool.ts
"""

from __future__ import annotations

from typing import Any

from hare.tools_impl.TaskTools.task_create_tool import get_task, deregister_task

TOOL_NAME = "TaskStop"
DESCRIPTION = "Stop a running or pending background task"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to stop (from TaskCreate output)",
            },
        },
        "required": ["task_id"],
    }


async def call(task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Stop a background task by ID."""
    task = get_task(task_id)
    if task is None:
        return {
            "task_id": task_id,
            "status": "not_found",
            "message": f"No task found with id '{task_id}'. Use TaskList to see running tasks.",
        }

    if task.status in ("completed", "failed", "cancelled"):
        return {
            "task_id": task_id,
            "status": task.status,
            "message": f"Task '{task.description}' was already {task.status}.",
        }

    # Cancel the running task
    if task._process and not task._process.done():
        task._process.cancel()

    task.status = "cancelled"
    import time
    task.completed_at = time.time()
    deregister_task(task_id)

    return {
        "task_id": task_id,
        "status": "cancelled",
        "message": f"Task '{task.description}' stopped.",
    }
