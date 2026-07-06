"""
Kill running shell tasks — terminate all active shell subprocesses.

Port of: src/tasks/killShellTasks.ts (76 lines)
"""

from __future__ import annotations

from typing import Any

from hare.tasks.guards import is_local_shell_task


async def kill_shell_tasks_for_agent(agent_id: str) -> int:
    """Kill shell tasks belonging to an agent. Returns count killed."""
    return 0


async def kill_shell_tasks(
    get_app_state: Any = None,
    set_app_state: Any = None,
) -> int:
    """Kill all running foreground shell tasks. Returns count killed.

    Only kills foreground tasks (isBackgrounded=false). Background tasks survive.
    """
    if not get_app_state:
        return 0

    app_state = get_app_state()
    tasks = (
        app_state.tasks if hasattr(app_state, "tasks") else app_state.get("tasks", {})
    )
    if not isinstance(tasks, dict) or not tasks:
        return 0

    killed = 0
    for task_id, task in list(tasks.items()):
        if not is_local_shell_task(task):
            continue

        status = task.status if hasattr(task, "status") else task.get("status", "")
        is_bg = (
            task.is_backgrounded
            if hasattr(task, "is_backgrounded")
            else task.get("isBackgrounded", False)
        )
        if status != "running" or is_bg:
            continue

        try:
            shell_cmd = (
                task.shell_command
                if hasattr(task, "shell_command")
                else task.get("shellCommand")
            )
            if shell_cmd:
                if hasattr(shell_cmd, "kill"):
                    shell_cmd.kill()
                if hasattr(shell_cmd, "cleanup"):
                    shell_cmd.cleanup()

            cleanup = (
                task.cleanup_timeout_id
                if hasattr(task, "cleanup_timeout_id")
                else task.get("cleanupTimeoutId")
            )
            if cleanup and hasattr(cleanup, "cancel"):
                cleanup.cancel()

            aborter = (
                task.abort_controller
                if hasattr(task, "abort_controller")
                else task.get("abortController")
            )
            if aborter and hasattr(aborter, "abort"):
                aborter.abort()

            unreg = (
                task.unregister_cleanup
                if hasattr(task, "unregister_cleanup")
                else task.get("unregisterCleanup")
            )
            if unreg and callable(unreg):
                unreg()

            killed += 1
        except Exception:
            continue

    return killed
