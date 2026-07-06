"""Dream task registry (port of src/tasks/DreamTask/DreamTask.ts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

DreamPhase = Literal["starting", "updating"]


@dataclass
class DreamTurn:
    text: str
    tool_use_count: int


@dataclass
class DreamTaskState:
    id: str
    type: Literal["dream"] = "dream"
    status: str = "running"
    phase: DreamPhase = "starting"
    sessions_reviewing: int = 0
    files_touched: list[str] = field(default_factory=list)
    turns: list[DreamTurn] = field(default_factory=list)
    prior_mtime: float = 0.0


def is_dream_task(task: object) -> bool:
    return isinstance(task, dict) and task.get("type") == "dream"


async def rollback_consolidation_lock(_prior_mtime: float) -> None:
    """Stub: wire to services/autoDream/consolidationLock."""
    return


def register_dream_task(
    _set_app_state: Callable[[Any], None],
    opts: dict[str, Any],
) -> str:
    _ = opts
    return "dream-task-id"


def add_dream_turn(
    _task_id: str,
    _turn: DreamTurn,
    _touched: list[str],
    _set_app_state: Callable[[Any], None],
) -> None:
    return


def complete_dream_task(_task_id: str, _set_app_state: Callable[[Any], None]) -> None:
    return


def fail_dream_task(_task_id: str, _set_app_state: Callable[[Any], None]) -> None:
    return


class DreamTask:
    name = "DreamTask"
    type = "dream"

    @staticmethod
    async def kill(task_id: str, set_app_state: Callable[[Any], None]) -> None:
        _ = (task_id, set_app_state)
