"""
Stop task logic — look up, validate, kill, and mark notified.

Port of: src/tasks/stopTask.ts (100 lines)
"""

from __future__ import annotations

from typing import Any


class StopTaskError(Exception):
    def __init__(self, message: str, code: str = "not_found") -> None:
        super().__init__(message)
        self.code = code


async def stop_task(
    task_id: str,
    get_app_state: Any = None,
    set_app_state: Any = None,
    get_task_by_type: Any = None,
) -> dict[str, Any]:
    """Look up task by ID, validate running, kill, mark notified.

    Raises StopTaskError(code='not_found'|'not_running'|'unsupported_type').
    """
    if not get_app_state:
        raise StopTaskError(f"No app state for task: {task_id}", "not_found")

    app_state = get_app_state()
    tasks = (
        app_state.tasks if hasattr(app_state, "tasks") else app_state.get("tasks", {})
    )
    task = tasks.get(task_id) if isinstance(tasks, dict) else None

    if not task:
        raise StopTaskError(f"No task found with ID: {task_id}", "not_found")

    task_status = task.status if hasattr(task, "status") else task.get("status", "")
    if task_status != "running":
        raise StopTaskError(
            f"Task {task_id} is not running (status: {task_status})", "not_running"
        )

    task_type = task.type if hasattr(task, "type") else task.get("type", "")
    if not get_task_by_type:
        raise StopTaskError(
            f"Cannot resolve task type: {task_type}", "unsupported_type"
        )

    task_impl = get_task_by_type(task_type) if callable(get_task_by_type) else None
    if not task_impl:
        raise StopTaskError(f"Unsupported task type: {task_type}", "unsupported_type")

    # Kill the task
    if hasattr(task_impl, "kill"):
        await task_impl.kill(task_id, set_app_state)

    # Shell: suppress exit-code noise, emit SDK task_terminated
    is_shell = task_type == "shell"
    if is_shell and set_app_state:
        suppressed = [False]

        def _mark(prev: Any) -> Any:
            t = (prev.tasks if hasattr(prev, "tasks") else prev.get("tasks", {})).get(
                task_id
            )
            if not t:
                return prev
            if hasattr(t, "notified") and t.notified:
                return prev
            if isinstance(t, dict) and t.get("notified"):
                return prev
            suppressed[0] = True
            if isinstance(t, dict):
                t["notified"] = True
            elif hasattr(t, "notified"):
                t.notified = True
            return prev

        set_app_state(_mark)

        if suppressed[0]:
            try:
                from hare.utils.sdk_event_queue import emit_task_terminated_sdk

                tool_use_id_val = (
                    task.tool_use_id
                    if hasattr(task, "tool_use_id")
                    else task.get("toolUseId")
                )
                desc_val = (
                    task.description
                    if hasattr(task, "description")
                    else task.get("description", "")
                )
                emit_task_terminated_sdk(
                    task_id,
                    "stopped",
                    tool_use_id=tool_use_id_val,
                    summary=desc_val,
                )
            except ImportError:
                pass

    cmd = (
        task.command
        if (is_shell and hasattr(task, "command"))
        else (task.description if hasattr(task, "description") else "")
    )
    return {"taskId": task_id, "taskType": task_type, "command": cmd}
