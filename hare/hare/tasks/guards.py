"""
Task type guards — discriminate between 7 task subtypes.

Port of: src/tasks/guards.ts + per-task-type guard files
"""

from __future__ import annotations

from typing import Any


def is_task_running(state: str) -> bool:
    return state in ("pending", "running")


def can_stop_task(state: str) -> bool:
    return state in ("pending", "running")


def is_task_done(state: str) -> bool:
    return state in ("completed", "failed", "cancelled")


def _get_type(task: Any) -> str:
    if hasattr(task, "type"):
        return task.type
    if isinstance(task, dict):
        return task.get("type", "")
    return ""


def is_local_shell_task(task: Any) -> bool:
    return _get_type(task) == "shell"


def is_local_agent_task(task: Any) -> bool:
    return _get_type(task) == "agent"


def is_remote_agent_task(task: Any) -> bool:
    return _get_type(task) == "remote_agent"


def is_in_process_teammate_task(task: Any) -> bool:
    return _get_type(task) == "in_process_teammate"


def is_local_workflow_task(task: Any) -> bool:
    return _get_type(task) == "local_workflow"


def is_monitor_mcp_task(task: Any) -> bool:
    return _get_type(task) == "monitor_mcp"


def is_dream_task(task: Any) -> bool:
    return _get_type(task) == "dream"
