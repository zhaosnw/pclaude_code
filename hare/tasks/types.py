"""
Task state types — union of all concrete task types.

Port of: src/tasks/types.ts (121 lines)
"""

from __future__ import annotations

from typing import Any, Literal

# ---------------------------------------------------------------------------
# Base task type enum
# ---------------------------------------------------------------------------

TaskType = Literal[
    "shell",
    "agent",
    "dream",
    "remote_agent",
    "in_process_teammate",
    "local_workflow",
    "monitor_mcp",
]

TaskStateStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


# ---------------------------------------------------------------------------
# Task state discriminated union (7 subtypes matching TS)
# ---------------------------------------------------------------------------


class TaskStateBase:
    """Base fields shared by all task states."""

    id: str = ""
    type: TaskType = "shell"
    status: TaskStateStatus = "pending"
    notified: bool = False
    is_backgrounded: bool = False
    tool_use_id: str | None = None
    description: str = ""


class LocalShellTaskState(TaskStateBase):
    """Shell command task."""

    type: Literal["shell"] = "shell"
    command: str = ""
    shell_command: Any = None
    cleanup_timeout_id: Any = None
    abort_controller: Any = None
    unregister_cleanup: Any = None
    output_buffer: list[str] = []
    pid: int | None = None
    start_time: float = 0.0


class LocalAgentTaskState(TaskStateBase):
    """Background agent task."""

    type: Literal["agent"] = "agent"
    agent_id: str = ""
    agent_type: str = ""
    prompt: str = ""
    model: str = ""
    messages: list[dict[str, Any]] = []
    result: Any = None


class RemoteAgentTaskState(TaskStateBase):
    """Cloud session task (ultraplan/PR review)."""

    type: Literal["remote_agent"] = "remote_agent"
    session_id: str = ""
    phase: str = ""
    repo_url: str = ""
    pr_number: str = ""


class InProcessTeammateTaskState(TaskStateBase):
    """In-process teammate lifecycle."""

    type: Literal["in_process_teammate"] = "in_process_teammate"
    identity: dict[str, Any] = {}
    pending_messages: list[dict[str, Any]] = []


class LocalWorkflowTaskState(TaskStateBase):
    """Workflow task."""

    type: Literal["local_workflow"] = "local_workflow"
    workflow_id: str = ""


class MonitorMcpTaskState(TaskStateBase):
    """MCP monitor task."""

    type: Literal["monitor_mcp"] = "monitor_mcp"
    server_name: str = ""


# Discriminated union
TaskState = (
    LocalShellTaskState
    | LocalAgentTaskState
    | RemoteAgentTaskState
    | InProcessTeammateTaskState
    | LocalWorkflowTaskState
    | MonitorMcpTaskState
)

BackgroundTaskState = TaskState  # All task types can be backgrounded


def is_background_task(task: TaskState) -> bool:
    """Check if a task should appear in the background tasks indicator."""
    if task.status not in ("running", "pending"):
        return False
    if hasattr(task, "is_backgrounded") and task.is_backgrounded is False:
        return False
    return True
