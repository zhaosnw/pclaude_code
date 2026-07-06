"""Scheduled tasks JSON + jitter (`cronTasks.ts`)."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hare.utils import cron as cron_mod
from hare.utils.debug import log_for_debugging
from hare.utils.errors import is_fs_inaccessible
from hare.utils.json_utils import safe_parse_json
from hare.utils.log import log_error


@dataclass
class CronTask:
    id: str
    cron: str
    prompt: str
    created_at: float
    last_fired_at: float | None = None
    recurring: bool | None = None
    permanent: bool | None = None
    durable: bool | None = None
    agent_id: str | None = None


CRON_FILE_REL = os.path.join(".hare", "scheduled_tasks.json")

DEFAULT_CRON_JITTER_CONFIG: dict[str, Any] = {
    "recurringFrac": 0.1,
    "recurringCapMs": 15 * 60 * 1000,
    "oneShotMaxMs": 90 * 1000,
    "oneShotFloorMs": 0,
    "oneShotMinuteMod": 30,
    "recurringMaxAgeMs": 7 * 24 * 60 * 60 * 1000,
}


def _project_root() -> str:
    try:
        from hare.bootstrap.state import get_project_root  # type: ignore[import-not-found]

        return get_project_root()
    except ImportError:
        return os.getcwd()


def get_cron_file_path(dir_path: str | None = None) -> str:
    return os.path.join(dir_path or _project_root(), CRON_FILE_REL)


def _task_to_json(t: CronTask) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": t.id,
        "cron": t.cron,
        "prompt": t.prompt,
        "createdAt": t.created_at,
    }
    if t.last_fired_at is not None:
        d["lastFiredAt"] = t.last_fired_at
    if t.recurring:
        d["recurring"] = True
    if t.permanent:
        d["permanent"] = True
    return d


async def read_cron_tasks(dir_path: str | None = None) -> list[CronTask]:
    path = get_cron_file_path(dir_path)
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        if is_fs_inaccessible(e):
            return []
        log_error(e)
        return []
    parsed = safe_parse_json(raw)
    if not isinstance(parsed, dict):
        return []
    tasks_raw = parsed.get("tasks")
    if not isinstance(tasks_raw, list):
        return []
    out: list[CronTask] = []
    for t in tasks_raw:
        if not isinstance(t, dict):
            continue
        if not all(k in t for k in ("id", "cron", "prompt", "createdAt")):
            log_for_debugging(
                f"[ScheduledTasks] skipping malformed task: {json.dumps(t)}"
            )
            continue
        if not cron_mod.parse_cron_expression(str(t["cron"])):
            log_for_debugging(
                f"[ScheduledTasks] skipping task {t['id']} with invalid cron '{t['cron']}'"
            )
            continue
        out.append(
            CronTask(
                id=str(t["id"]),
                cron=str(t["cron"]),
                prompt=str(t["prompt"]),
                created_at=float(t["createdAt"]),
                last_fired_at=float(t["lastFiredAt"])
                if t.get("lastFiredAt") is not None
                else None,
                recurring=bool(t["recurring"]) if t.get("recurring") else None,
                permanent=bool(t["permanent"]) if t.get("permanent") else None,
            )
        )
    return out


def has_cron_tasks_sync(dir_path: str | None = None) -> bool:
    path = get_cron_file_path(dir_path)
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    parsed = safe_parse_json(raw)
    if not isinstance(parsed, dict):
        return False
    tasks = parsed.get("tasks")
    return isinstance(tasks, list) and len(tasks) > 0


async def write_cron_tasks(tasks: list[CronTask], dir_path: str | None = None) -> None:
    root = dir_path or _project_root()
    os.makedirs(os.path.join(root, ".hare"), exist_ok=True)
    body = {"tasks": [_task_to_json(t) for t in tasks]}
    Path(get_cron_file_path(root)).write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )


def next_cron_run_ms(cron: str, from_ms: float) -> float | None:
    return cron_mod.next_cron_run_ms(cron, from_ms)


def jitter_frac(task_id: str) -> float:
    try:
        return int(task_id[:8], 16) / float(0x100000000)
    except ValueError:
        return 0.0


def jittered_next_cron_run_ms(
    cron: str,
    from_ms: float,
    task_id: str,
    cfg: dict[str, Any] | None = None,
) -> float | None:
    cfg = cfg or DEFAULT_CRON_JITTER_CONFIG
    t1 = next_cron_run_ms(cron, from_ms)
    if t1 is None:
        return None
    t2 = next_cron_run_ms(cron, t1 + 1)
    if t2 is None:
        return t1
    jitter = min(
        jitter_frac(task_id) * float(cfg["recurringFrac"]) * (t2 - t1),
        float(cfg["recurringCapMs"]),
    )
    return t1 + jitter


def one_shot_jittered_next_cron_run_ms(
    cron: str,
    from_ms: float,
    task_id: str,
    cfg: dict[str, Any] | None = None,
) -> float | None:
    from datetime import datetime

    cfg = cfg or DEFAULT_CRON_JITTER_CONFIG
    t1 = next_cron_run_ms(cron, from_ms)
    if t1 is None:
        return None
    if datetime.fromtimestamp(t1 / 1000.0).minute % int(cfg["oneShotMinuteMod"]) != 0:
        return t1
    lead = float(cfg["oneShotFloorMs"]) + jitter_frac(task_id) * (
        float(cfg["oneShotMaxMs"]) - float(cfg["oneShotFloorMs"])
    )
    return max(t1 - lead, from_ms)


def find_missed_tasks(tasks: list[CronTask], now_ms: float) -> list[CronTask]:
    return [
        t
        for t in tasks
        if (n := next_cron_run_ms(t.cron, t.created_at)) is not None and n < now_ms
    ]


async def add_cron_task(
    cron: str,
    prompt: str,
    recurring: bool,
    durable: bool,
    agent_id: str | None = None,
) -> str:
    cid = uuid.uuid4().hex[:8]
    task = CronTask(
        id=cid,
        cron=cron,
        prompt=prompt,
        created_at=__import__("time").time() * 1000,
        recurring=recurring if recurring else None,
        agent_id=agent_id,
    )
    if not durable:
        try:
            from hare.bootstrap.state import add_session_cron_task  # type: ignore[import-not-found]

            add_session_cron_task(task)
        except ImportError:
            pass
        return cid
    tasks = await read_cron_tasks()
    tasks.append(task)
    await write_cron_tasks(tasks)
    return cid


async def remove_cron_tasks(ids: list[str], dir_path: str | None = None) -> None:
    if not ids:
        return
    id_set = set(ids)
    tasks = await read_cron_tasks(dir_path)
    remaining = [t for t in tasks if t.id not in id_set]
    if len(remaining) == len(tasks):
        return
    await write_cron_tasks(remaining, dir_path)


async def mark_cron_tasks_fired(
    ids: list[str], fired_at: float, dir_path: str | None = None
) -> None:
    if not ids:
        return
    id_set = set(ids)
    tasks = await read_cron_tasks(dir_path)
    changed = False
    for t in tasks:
        if t.id in id_set:
            t.last_fired_at = fired_at
            changed = True
    if changed:
        await write_cron_tasks(tasks, dir_path)


async def list_all_cron_tasks(dir_path: str | None = None) -> list[CronTask]:
    return await read_cron_tasks(dir_path)
