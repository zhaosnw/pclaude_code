"""
TaskStopTool – stop a running background task.

Port of: src/tools/TaskStopTool/TaskStopTool.ts
"""

from __future__ import annotations

from typing import Any

from hare.state.app_state import get_app_state, set_app_state
from hare.tasks.guards import can_stop_task, is_local_shell_task, _get_type
from hare.utils.sdk_event_queue import emit_task_terminated_sdk

TOOL_NAME = "TaskStop"
ALIASES = ["KillShell"]


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the background task to stop",
            },
            "shell_id": {
                "type": "string",
                "description": "Deprecated: use task_id instead",
            },
        },
        "required": [],
    }


async def call(task_id: str = "", shell_id: str = "", **kwargs: Any) -> dict[str, Any]:
    """Stop a running background task by its ID.

    Looks up the task in app state, validates it is stoppable, kills the
    underlying process (shell tasks) or cancels (agent/teammate tasks),
    marks the task as notified, and emits an SDK termination event.

    Returns structured result with task_id, task_type, description, and
    status evidence so the caller can confirm the outcome.
    """
    target_id = task_id or shell_id
    if not target_id:
        return {
            "success": False,
            "error": "Must provide task_id (shell_id is deprecated)",
        }

    state = get_app_state()
    tasks: dict[str, Any] = getattr(state, "tasks", {})

    if not isinstance(tasks, dict) or target_id not in tasks:
        # Fallback: background shell tasks started by BashTool(run_in_background)
        # live in the TaskTools registry (read by TaskOutput), not app_state.
        from hare.tools_impl.TaskTools.task_create_tool import get_task as _get_tt

        tt = _get_tt(target_id)
        if tt is not None:
            proc_task = getattr(tt, "_process", None)
            if proc_task is not None and not proc_task.done():
                proc_task.cancel()
            if getattr(tt, "status", "") in ("running", "pending"):
                tt.status = "cancelled"
            return {
                "success": True,
                "task_id": target_id,
                "task_type": "local_shell",
                "status": "cancelled",
                "result": "terminated",
            }
        return {
            "success": False,
            "task_id": target_id,
            "error": f"No task found with ID: {target_id}",
        }

    task = tasks[target_id]
    task_status: str = (
        task.status if hasattr(task, "status") else task.get("status", "")
    )
    task_type: str = _get_type(task)

    if not can_stop_task(task_status):
        return {
            "success": False,
            "task_id": target_id,
            "task_type": task_type,
            "status": task_status,
            "error": f"Task {target_id} cannot be stopped (status: {task_status})",
        }

    tool_use_id: str | None = (
        task.tool_use_id if hasattr(task, "tool_use_id") else task.get("toolUseId")
    )
    description: str = (
        task.description if hasattr(task, "description") else task.get("description", "")
    )

    # ------------------------------------------------------------------
    # Kill the underlying process / cancel the task
    # ------------------------------------------------------------------
    kill_result: str = "terminated"

    if is_local_shell_task(task):
        kill_result = await _kill_shell_task(task)

    # Agent, teammate, workflow, monitor tasks: mark cancelled
    if task_type in ("agent", "remote_agent", "in_process_teammate",
                     "local_workflow", "monitor_mcp", "dream"):
        kill_result = "cancelled"

    # ------------------------------------------------------------------
    # Update state: mark as cancelled and notified
    # ------------------------------------------------------------------

    def _mark_stopped(prev: Any) -> Any:
        t = getattr(prev, "tasks", {}).get(target_id)
        if t is None:
            return prev
        if hasattr(t, "status"):
            t.status = "cancelled"
        elif isinstance(t, dict):
            t["status"] = "cancelled"
        if hasattr(t, "notified"):
            t.notified = True
        elif isinstance(t, dict):
            t["notified"] = True
        return prev

    set_app_state(_mark_stopped)

    # ------------------------------------------------------------------
    # Emit SDK termination event if this was a shell task
    # ------------------------------------------------------------------
    if is_local_shell_task(task):
        try:
            emit_task_terminated_sdk(
                target_id,
                "stopped",
                tool_use_id=tool_use_id,
                summary=description,
            )
        except Exception:
            pass

    return {
        "success": True,
        "task_id": target_id,
        "task_type": task_type,
        "description": description,
        "result": kill_result,
    }


async def _kill_shell_task(task: Any) -> str:
    """Kill the subprocess and clean up resources for a shell task.

    Returns 'killed' on subprocess kill, 'terminated' otherwise.
    """
    result = "terminated"

    # 1. Kill shell command subprocess
    shell_cmd = (
        task.shell_command if hasattr(task, "shell_command") else task.get("shellCommand")
    )
    if shell_cmd:
        if hasattr(shell_cmd, "kill"):
            try:
                shell_cmd.kill()
                result = "killed"
            except Exception:
                pass
        if hasattr(shell_cmd, "cleanup"):
            try:
                shell_cmd.cleanup()
            except Exception:
                pass

    # 2. Cancel cleanup timeout
    cleanup = (
        task.cleanup_timeout_id
        if hasattr(task, "cleanup_timeout_id")
        else task.get("cleanupTimeoutId")
    )
    if cleanup and hasattr(cleanup, "cancel"):
        try:
            cleanup.cancel()
        except Exception:
            pass

    # 3. Abort via abort controller
    aborter = (
        task.abort_controller
        if hasattr(task, "abort_controller")
        else task.get("abortController")
    )
    if aborter and hasattr(aborter, "abort"):
        try:
            aborter.abort()
        except Exception:
            pass

    # 4. Unregister cleanup callback
    unreg = (
        task.unregister_cleanup
        if hasattr(task, "unregister_cleanup")
        else task.get("unregisterCleanup")
    )
    if unreg and callable(unreg):
        try:
            unreg()
        except Exception:
            pass

    return result
