"""
Teammate view helpers — enter/exit teammate transcript view, dismiss agents.

Port of: src/state/teammateViewHelpers.ts (141 lines)

Manages the UI state transitions for viewing teammate agent transcripts.
"""

from __future__ import annotations

from typing import Any

PANEL_GRACE_MS = 30_000


def _is_local_agent(task: Any) -> bool:
    return isinstance(task, dict) and task.get("type") == "local_agent"


def _is_terminal_task_status(status: str) -> bool:
    return status in ("completed", "failed", "interrupted", "cancelled", "error")


def _release(task: dict[str, Any]) -> dict[str, Any]:
    if "retain" not in task and "messages" not in task:
        task = dict(task)
    return {
        **task,
        "retain": False,
        "messages": None,
        "diskLoaded": False,
        "evictAfter": (__import__("time").time() * 1000 + PANEL_GRACE_MS)
        if _is_terminal_task_status(task.get("status", ""))
        else None,
    }


def enter_teammate_view(
    task_id: str,
    set_app_state: Any,
) -> None:
    """Enter teammate transcript view. Sets retain, clears evictAfter."""

    def _updater(prev: dict[str, Any]) -> dict[str, Any]:
        task = prev.get("tasks", {}).get(task_id, {})
        prev_id = prev.get("viewingAgentTaskId")
        prev_task = prev.get("tasks", {}).get(prev_id) if prev_id else None
        switching = (
            prev_id is not None
            and prev_id != task_id
            and _is_local_agent(prev_task)
            and prev_task.get("retain")
        )
        needs_retain = _is_local_agent(task) and (
            not task.get("retain") or task.get("evictAfter") is not None
        )
        needs_view = (
            prev.get("viewingAgentTaskId") != task_id
            or prev.get("viewSelectionMode") != "viewing-agent"
        )
        if not needs_retain and not needs_view and not switching:
            return prev

        tasks = dict(prev.get("tasks", {}))
        if switching or needs_retain:
            if switching:
                tasks[prev_id] = _release(prev_task)
            if needs_retain:
                tasks[task_id] = {**task, "retain": True, "evictAfter": None}

        return {
            **prev,
            "viewingAgentTaskId": task_id,
            "viewSelectionMode": "viewing-agent",
            "tasks": tasks,
        }

    set_app_state(_updater)


def exit_teammate_view(set_app_state: Any) -> None:
    """Exit teammate transcript view back to leader view."""

    def _updater(prev: dict[str, Any]) -> dict[str, Any]:
        agent_id = prev.get("viewingAgentTaskId")
        cleared = {
            **prev,
            "viewingAgentTaskId": None,
            "viewSelectionMode": "none",
        }
        if agent_id is None:
            return prev if prev.get("viewSelectionMode") == "none" else cleared
        task = prev.get("tasks", {}).get(agent_id, {})
        if not _is_local_agent(task) or not task.get("retain"):
            return cleared
        return {
            **cleared,
            "tasks": {**prev.get("tasks", {}), agent_id: _release(task)},
        }

    set_app_state(_updater)


def stop_or_dismiss_agent(task_id: str, set_app_state: Any) -> None:
    """Running → abort, terminal → dismiss. Also exits view if viewing the dismissed agent."""

    def _updater(prev: dict[str, Any]) -> dict[str, Any]:
        task = prev.get("tasks", {}).get(task_id, {})
        if not _is_local_agent(task):
            return prev
        if task.get("status") == "running":
            aborter = task.get("abortController")
            if aborter and hasattr(aborter, "abort"):
                aborter.abort()
            return prev
        if task.get("evictAfter") == 0:
            return prev
        viewing_this = prev.get("viewingAgentTaskId") == task_id
        result = {
            **prev,
            "tasks": {
                **prev.get("tasks", {}),
                task_id: {**_release(task), "evictAfter": 0},
            },
        }
        if viewing_this:
            result["viewingAgentTaskId"] = None
            result["viewSelectionMode"] = "none"
        return result

    set_app_state(_updater)
