"""In-process teammate helpers — port of `inProcessTeammateHelpers.ts`."""

from __future__ import annotations

from typing import Any, Callable


def is_permission_response(message_text: str) -> bool | None:
    """Wire `teammateMailbox.isPermissionResponse`."""
    return None


def is_sandbox_permission_response(message_text: str) -> bool | None:
    """Wire `teammateMailbox.isSandboxPermissionResponse`."""
    return None


def find_in_process_teammate_task_id(agent_name: str, app_state: Any) -> str | None:
    tasks = getattr(app_state, "tasks", None) or {}
    for task in tasks.values():
        ident = getattr(task, "identity", None)
        if ident and getattr(ident, "agent_name", None) == agent_name:
            return str(getattr(task, "id", "")) or None
    return None


def set_awaiting_plan_approval(
    task_id: str,
    set_app_state: Callable[[Callable[[Any], Any]], None],
    awaiting: bool,
) -> None:
    try:
        from hare.utils.task.framework import update_task_state  # type: ignore[import-not-found]
    except ImportError:

        def update_task_state(*_a: Any, **_k: Any) -> None:  # type: ignore[misc, no-redef]
            pass

    def _upd(task: Any) -> Any:
        import copy

        out = copy.copy(task)
        setattr(out, "awaiting_plan_approval", awaiting)
        return out

    update_task_state(task_id, set_app_state, _upd)


def handle_plan_approval_response(
    task_id: str,
    _response: Any,
    set_app_state: Callable[[Callable[[Any], Any]], None],
) -> None:
    set_awaiting_plan_approval(task_id, set_app_state, False)


def is_permission_related_response(message_text: str) -> bool:
    a = is_permission_response(message_text)
    b = is_sandbox_permission_response(message_text)
    return bool(a or b)
