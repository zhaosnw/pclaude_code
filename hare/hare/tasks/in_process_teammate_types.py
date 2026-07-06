"""In-process teammate task types (port of src/tasks/InProcessTeammateTask/types.ts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class TeammateIdentity:
    agent_id: str
    agent_name: str
    team_name: str
    color: str | None = None
    plan_mode_required: bool = False
    parent_session_id: str = ""


@dataclass
class InProcessTeammateTaskState:
    id: str
    type: str = "in_process_teammate"
    identity: TeammateIdentity = field(
        default_factory=lambda: TeammateIdentity("", "", "")
    )
    prompt: str = ""
    model: str | None = None
    selected_agent: Any | None = None
    awaiting_plan_approval: bool = False
    permission_mode: str = "default"
    messages: list[Any] | None = None
    pending_user_messages: list[str] = field(default_factory=list)
    is_idle: bool = False
    shutdown_requested: bool = False
    last_reported_tool_count: int = 0
    last_reported_token_count: int = 0


TEAMMATE_MESSAGES_UI_CAP = 50


def is_in_process_teammate_task(task: object) -> bool:
    return isinstance(task, dict) and task.get("type") == "in_process_teammate"


def append_capped_message(prev: list[T] | None, item: T) -> list[T]:
    if not prev:
        return [item]
    if len(prev) >= TEAMMATE_MESSAGES_UI_CAP:
        return prev[-(TEAMMATE_MESSAGES_UI_CAP - 1) :] + [item]
    return [*prev, item]
