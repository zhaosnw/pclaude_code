"""
Tests for tasks/types.py — task state discriminated union.
"""

from __future__ import annotations

from hare.tasks.types import (
    BackgroundTaskState,
    InProcessTeammateTaskState,
    LocalAgentTaskState,
    LocalShellTaskState,
    LocalWorkflowTaskState,
    MonitorMcpTaskState,
    RemoteAgentTaskState,
    TaskState,
    TaskStateBase,
    TaskStateStatus,
    TaskType,
    is_background_task,
)


class TestTaskStateBase:
    def test_defaults(self) -> None:
        ts = TaskStateBase()
        assert ts.type == "shell"
        assert ts.status == "pending"
        assert ts.notified is False
        assert ts.is_backgrounded is False
        assert ts.tool_use_id is None
        assert ts.description == ""

    def test_custom_values(self) -> None:
        ts = TaskStateBase()
        ts.id = "task-1"
        ts.type = "agent"
        ts.status = "running"
        ts.description = "test task"
        assert ts.id == "task-1"
        assert ts.type == "agent"
        assert ts.description == "test task"


class TestTaskTypes:
    def test_all_task_types_have_correct_type(self) -> None:
        assert LocalShellTaskState().type == "shell"
        assert LocalAgentTaskState().type == "agent"
        assert RemoteAgentTaskState().type == "remote_agent"
        assert InProcessTeammateTaskState().type == "in_process_teammate"
        assert LocalWorkflowTaskState().type == "local_workflow"
        assert MonitorMcpTaskState().type == "monitor_mcp"

    def test_local_shell_task_state(self) -> None:
        ts = LocalShellTaskState()
        ts.command = "echo hello"
        ts.pid = 1234
        assert ts.command == "echo hello"
        assert ts.pid == 1234
        assert ts.output_buffer == []

    def test_local_agent_task_state(self) -> None:
        ts = LocalAgentTaskState()
        ts.agent_id = "agent-1"
        ts.prompt = "do something"
        ts.model = "sonnet"
        assert ts.agent_id == "agent-1"
        assert ts.messages == []

    def test_remote_agent_task_state(self) -> None:
        ts = RemoteAgentTaskState()
        ts.session_id = "s1"
        ts.repo_url = "https://github.com/example/repo"
        assert ts.session_id == "s1"
        assert ts.pr_number == ""

    def test_in_process_teammate_task_state(self) -> None:
        ts = InProcessTeammateTaskState()
        ts.identity = {"name": "teammate1"}
        assert ts.identity == {"name": "teammate1"}
        assert ts.pending_messages == []

    def test_local_workflow_task_state(self) -> None:
        ts = LocalWorkflowTaskState()
        ts.workflow_id = "wf-1"
        assert ts.workflow_id == "wf-1"

    def test_monitor_mcp_task_state(self) -> None:
        ts = MonitorMcpTaskState()
        ts.server_name = "mcp-server-1"
        assert ts.server_name == "mcp-server-1"


class TestIsBackgroundTask:
    def test_running_non_backgrounded_returns_false(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "running"
        ts.is_backgrounded = False
        assert is_background_task(ts) is False

    def test_running_backgrounded_returns_true(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "running"
        ts.is_backgrounded = True
        assert is_background_task(ts) is True

    def test_pending_explicitly_not_backgrounded_returns_false(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "pending"
        ts.is_backgrounded = False
        # is_background_task explicitly checks for is False
        assert is_background_task(ts) is False

    def test_pending_default_is_backgrounded_is_false(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "pending"
        # Default is_backgrounded is False, so is_background_task returns False
        assert is_background_task(ts) is False

    def test_pending_with_backgrounded_true(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "pending"
        ts.is_backgrounded = True
        assert is_background_task(ts) is True

    def test_completed_returns_false(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "completed"
        ts.is_backgrounded = True
        assert is_background_task(ts) is False

    def test_failed_returns_false(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "failed"
        ts.is_backgrounded = True
        assert is_background_task(ts) is False

    def test_cancelled_returns_false(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "cancelled"
        ts.is_backgrounded = True
        assert is_background_task(ts) is False

    def test_explicitly_not_backgrounded_returns_false(self) -> None:
        ts = LocalShellTaskState()
        ts.status = "running"
        ts.is_backgrounded = False
        assert is_background_task(ts) is False

    def test_agent_task_background_check(self) -> None:
        ts = LocalAgentTaskState()
        ts.status = "running"
        ts.is_backgrounded = True
        assert is_background_task(ts) is True
