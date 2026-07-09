"""
Tests for tasks/guards.py and tasks/types.py — task type guards and types.
"""

from __future__ import annotations

from hare.tasks.guards import (
    is_task_running,
    can_stop_task,
    is_task_done,
    is_local_shell_task,
    is_local_agent_task,
    is_remote_agent_task,
    is_in_process_teammate_task,
    is_local_workflow_task,
    is_monitor_mcp_task,
    is_dream_task,
)


# ---------------------------------------------------------------------------
# Status check functions
# ---------------------------------------------------------------------------


class TestTaskStatusGuards:
    def test_is_task_running_pending(self) -> None:
        assert is_task_running("pending") is True

    def test_is_task_running_running(self) -> None:
        assert is_task_running("running") is True

    def test_is_task_running_completed(self) -> None:
        assert is_task_running("completed") is False

    def test_is_task_running_failed(self) -> None:
        assert is_task_running("failed") is False

    def test_can_stop_task_pending(self) -> None:
        assert can_stop_task("pending") is True

    def test_can_stop_task_running(self) -> None:
        assert can_stop_task("running") is True

    def test_can_stop_task_completed(self) -> None:
        assert can_stop_task("completed") is False

    def test_is_task_done_completed(self) -> None:
        assert is_task_done("completed") is True

    def test_is_task_done_failed(self) -> None:
        assert is_task_done("failed") is True

    def test_is_task_done_cancelled(self) -> None:
        assert is_task_done("cancelled") is True

    def test_is_task_done_pending(self) -> None:
        assert is_task_done("pending") is False


# ---------------------------------------------------------------------------
# Type guards via attribute access
# ---------------------------------------------------------------------------


class _HasType:
    def __init__(self, type_val: str) -> None:
        self.type = type_val


class TestTaskTypeGuardsViaAttribute:
    def test_is_local_shell_task(self) -> None:
        assert is_local_shell_task(_HasType("shell")) is True
        assert is_local_shell_task(_HasType("agent")) is False

    def test_is_local_agent_task(self) -> None:
        assert is_local_agent_task(_HasType("agent")) is True
        assert is_local_agent_task(_HasType("shell")) is False

    def test_is_remote_agent_task(self) -> None:
        assert is_remote_agent_task(_HasType("remote_agent")) is True
        assert is_remote_agent_task(_HasType("agent")) is False

    def test_is_in_process_teammate_task(self) -> None:
        assert is_in_process_teammate_task(_HasType("in_process_teammate")) is True
        assert is_in_process_teammate_task(_HasType("shell")) is False

    def test_is_local_workflow_task(self) -> None:
        assert is_local_workflow_task(_HasType("local_workflow")) is True
        assert is_local_workflow_task(_HasType("shell")) is False

    def test_is_monitor_mcp_task(self) -> None:
        assert is_monitor_mcp_task(_HasType("monitor_mcp")) is True
        assert is_monitor_mcp_task(_HasType("shell")) is False

    def test_is_dream_task(self) -> None:
        assert is_dream_task(_HasType("dream")) is True
        assert is_dream_task(_HasType("shell")) is False


# ---------------------------------------------------------------------------
# Type guards via dict access (for plain dict task objects)
# ---------------------------------------------------------------------------


class TestTaskTypeGuardsViaDict:
    def test_is_local_shell_task_dict(self) -> None:
        assert is_local_shell_task({"type": "shell"}) is True
        assert is_local_shell_task({"type": "agent"}) is False

    def test_is_local_agent_task_dict(self) -> None:
        assert is_local_agent_task({"type": "agent"}) is True

    def test_is_remote_agent_task_dict(self) -> None:
        assert is_remote_agent_task({"type": "remote_agent"}) is True

    def test_is_in_process_teammate_task_dict(self) -> None:
        assert is_in_process_teammate_task({"type": "in_process_teammate"}) is True

    def test_is_local_workflow_task_dict(self) -> None:
        assert is_local_workflow_task({"type": "local_workflow"}) is True

    def test_is_monitor_mcp_task_dict(self) -> None:
        assert is_monitor_mcp_task({"type": "monitor_mcp"}) is True

    def test_is_dream_task_dict(self) -> None:
        assert is_dream_task({"type": "dream"}) is True


# ---------------------------------------------------------------------------
# Edge cases — unknown/invalid tasks
# ---------------------------------------------------------------------------


class TestTaskTypeGuardsEdgeCases:
    def test_dict_without_type_key(self) -> None:
        assert is_local_shell_task({}) is False
        assert is_dream_task({}) is False

    def test_unknown_type_returns_false(self) -> None:
        assert is_local_shell_task(_HasType("unknown")) is False
        assert is_local_agent_task({"type": "not_a_task"}) is False
