"""Main session background task helpers (port of src/tasks/LocalMainSessionTask.ts)."""

from __future__ import annotations

import secrets
from string import ascii_lowercase, digits
from typing import Any, Callable

_ALPHABET = digits + ascii_lowercase


def generate_main_session_task_id() -> str:
    b = secrets.token_bytes(8)
    s = "s"
    for byte in b:
        s += _ALPHABET[byte % len(_ALPHABET)]
    return s


def register_main_session_task(
    description: str,
    set_app_state: Callable[[Any], None],
    main_thread_agent: Any | None = None,
    existing_abort: Any | None = None,
) -> dict[str, Any]:
    _ = (description, set_app_state, main_thread_agent)
    task_id = generate_main_session_task_id()
    return {"task_id": task_id, "abort_signal": getattr(existing_abort, "signal", None)}


def complete_main_session_task(
    task_id: str,
    success: bool,
    set_app_state: Callable[[Any], None],
) -> None:
    _ = (task_id, success, set_app_state)


def foreground_main_session_task(
    task_id: str,
    set_app_state: Callable[[Any], None],
) -> list[Any] | None:
    _ = (task_id, set_app_state)
    return None


def is_main_session_task(task: object) -> bool:
    return (
        isinstance(task, dict)
        and task.get("type") == "local_agent"
        and task.get("agentType") == "main-session"
    )


async def start_background_session(**kwargs: Any) -> str:
    _ = kwargs
    return generate_main_session_task_id()


class LocalMainSessionTask:
    """Local main session background task (TS parity class, P2 — stub)."""

    def __init__(self, **kwargs: Any) -> None:
        self.task_id = generate_main_session_task_id()
        self.description = kwargs.get("description", "")
        self.status = "pending"
