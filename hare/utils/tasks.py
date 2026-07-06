"""File-backed task lists for swarms (port of tasks.ts)."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from hare.bootstrap import state as bootstrap_state
from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir, is_env_truthy
from hare.utils.slow_operations import json_parse, json_stringify
from hare.utils.teammate import get_team_name
from hare.utils.teammate_context import get_teammate_context

TaskStatus = Literal["pending", "in_progress", "completed"]

_leader_team_name: str | None = None

LOCK_OPTIONS = {"retries": {"retries": 30, "minTimeout": 5, "maxTimeout": 100}}

HIGH_WATER_MARK_FILE = ".highwatermark"
DEFAULT_TASKS_MODE_TASK_LIST_ID = "tasklist"


def set_leader_team_name(team_name: str) -> None:
    global _leader_team_name
    if _leader_team_name == team_name:
        return
    _leader_team_name = team_name
    notify_tasks_updated()


def clear_leader_team_name() -> None:
    global _leader_team_name
    if _leader_team_name is None:
        return
    _leader_team_name = None
    notify_tasks_updated()


def on_tasks_updated(cb: Any) -> Any:
    _ = cb
    return lambda: None


def notify_tasks_updated() -> None:
    pass


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: TaskStatus
    blocks: list[str]
    blocked_by: list[str]
    active_form: str | None = None
    owner: str | None = None
    metadata: dict[str, Any] | None = None


def sanitize_path_component(input_str: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", input_str)


def get_tasks_dir(task_list_id: str) -> str:
    return str(
        Path(get_hare_config_home_dir())
        / "tasks"
        / sanitize_path_component(task_list_id)
    )


def get_task_path(task_list_id: str, task_id: str) -> str:
    return str(
        Path(get_tasks_dir(task_list_id)) / f"{sanitize_path_component(task_id)}.json"
    )


def get_task_list_id() -> str:
    if os.environ.get("CLAUDE_CODE_TASK_LIST_ID"):
        return os.environ["CLAUDE_CODE_TASK_LIST_ID"]
    ctx = get_teammate_context()
    if ctx:
        return ctx.team_name
    return get_team_name() or _leader_team_name or bootstrap_state.get_session_id()


def is_todo_v2_enabled() -> bool:
    if is_env_truthy(os.environ.get("CLAUDE_CODE_ENABLE_TASKS")):
        return True
    return not bootstrap_state.get_is_non_interactive_session()


async def ensure_tasks_dir(task_list_id: str) -> None:
    Path(get_tasks_dir(task_list_id)).mkdir(parents=True, exist_ok=True)


async def list_tasks(task_list_id: str) -> list[Task]:
    d = Path(get_tasks_dir(task_list_id))
    if not d.is_dir():
        return []
    out: list[Task] = []
    for p in d.glob("*.json"):
        if p.name.startswith("."):
            continue
        try:
            data = json_parse(p.read_text(encoding="utf-8"))
            out.append(Task(**data))
        except Exception as e:
            log_for_debugging(f"[Tasks] skip {p}: {e}")
    return out


async def create_task(task_list_id: str, task_data: dict[str, Any]) -> str:
    await ensure_tasks_dir(task_list_id)
    tid = str(len(await list_tasks(task_list_id)) + 1)
    task = Task(
        id=tid,
        subject=task_data["subject"],
        description=task_data["description"],
        status=task_data["status"],
        blocks=list(task_data.get("blocks", [])),
        blocked_by=list(task_data.get("blocked_by", [])),
        active_form=task_data.get("active_form"),
        owner=task_data.get("owner"),
        metadata=task_data.get("metadata"),
    )
    Path(get_task_path(task_list_id, tid)).write_text(
        json_stringify(asdict(task), indent=2), encoding="utf-8"
    )
    notify_tasks_updated()
    return tid
