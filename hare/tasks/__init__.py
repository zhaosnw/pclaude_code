"""Task registry — get tasks by type.

Port of: src/tasks.ts (39 lines)
"""

from typing import Any

from hare.tasks.types import TaskState, TaskType
from hare.tasks.guards import is_task_running, can_stop_task
from hare.tasks.stop_task import stop_task


def get_all_tasks() -> list[Any]:
    """Get all registered task implementations. Mirrors TS getAllTasks()."""
    tasks: list[Any] = []
    try:
        from hare.tasks.local_main_session_task import LocalMainSessionTask

        tasks.append(LocalMainSessionTask())
    except (ImportError, AttributeError):
        pass
    try:
        from hare.tasks.dream_task import DreamTask

        tasks.append(DreamTask())
    except (ImportError, AttributeError):
        pass
    # Feature-gated: LocalWorkflowTask (WORKFLOW_SCRIPTS gate)
    # Feature-gated: MonitorMcpTask (MONITOR_TOOL gate)
    return tasks


def get_task_by_type(task_type: str) -> Any:
    """Get a task implementation by its type string."""
    for task in get_all_tasks():
        if hasattr(task, "type") and task.type == task_type:
            return task
    return None
