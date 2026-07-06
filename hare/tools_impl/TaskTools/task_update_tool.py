"""Task Update Tool — send a message or update to a running task.

Port of: src/tools/TaskUpdateTool/TaskUpdateTool.ts
"""

from __future__ import annotations

from typing import Any

from hare.tools_impl.TaskTools.task_create_tool import get_task

TOOL_NAME = "TaskUpdate"
DESCRIPTION = "Send a message or update to a running background task"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to update",
            },
            "message": {
                "type": "string",
                "description": "Message to send to the task",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "cancelled"],
                "description": "New status for the task (optional)",
            },
        },
        "required": ["task_id"],
    }


async def call(task_id: str, message: str = "", status: str = "", **kwargs: Any) -> dict[str, Any]:
    """Update a task's message or status."""
    task = get_task(task_id)
    if task is None:
        return {
            "task_id": task_id,
            "status": "not_found",
            "message": f"No task found with id '{task_id}'.",
        }

    updates = []
    if message:
        task.result = f"[Update] {message}"
        updates.append("message sent")
    if status:
        valid = {"pending", "running", "completed", "cancelled"}
        if status in valid:
            task.status = status
            updates.append(f"status -> {status}")

    if not updates:
        return {
            "task_id": task_id,
            "status": task.status,
            "message": "No changes provided. Use 'message' or 'status' fields.",
        }

    return {
        "task_id": task_id,
        "status": task.status,
        "message": f"Task updated: {', '.join(updates)}.",
    }
