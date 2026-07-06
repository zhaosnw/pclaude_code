"""Scheduler lock file (`cronTasksLock.ts`)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hare.utils.cleanup_registry import register_cleanup
from hare.utils.debug import log_for_debugging
from hare.utils.errors import get_errno_code
from hare.utils.generic_process_utils import is_process_running
from hare.utils.json_utils import safe_parse_json

LOCK_FILE_REL = os.path.join(".hare", "scheduled_tasks.lock")


def _lock_path(dir_path: str | None) -> str:
    try:
        from hare.bootstrap.state import get_project_root  # type: ignore[import-not-found]

        root = dir_path or get_project_root()
    except ImportError:
        root = dir_path or os.getcwd()
    return os.path.join(root, LOCK_FILE_REL)


def _session_id(opts: dict[str, Any] | None) -> str:
    if opts and opts.get("lock_identity"):
        return str(opts["lock_identity"])
    try:
        from hare.bootstrap.state import get_session_id  # type: ignore[import-not-found]

        return get_session_id()
    except ImportError:
        return "local"


_unregister: Any = None


async def try_acquire_scheduler_lock(opts: dict[str, Any] | None = None) -> bool:
    dir_path = opts.get("dir") if opts else None
    path = Path(_lock_path(dir_path))
    sid = _session_id(opts or {})
    lock_body = json.dumps(
        {
            "sessionId": sid,
            "pid": os.getpid(),
            "acquiredAt": __import__("time").time() * 1000,
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as f:
            f.write(lock_body)
        log_for_debugging(
            f"[ScheduledTasks] acquired scheduler lock (PID {os.getpid()})"
        )

        async def _release() -> None:
            await release_scheduler_lock(opts)

        global _unregister
        _unregister = register_cleanup(_release)
        return True
    except FileExistsError:
        try:
            raw = path.read_text(encoding="utf-8")
            data = safe_parse_json(raw)
            if isinstance(data, dict) and data.get("sessionId") == sid:
                path.write_text(lock_body, encoding="utf-8")
                return True
            pid = int(data.get("pid", 0)) if isinstance(data, dict) else 0
            if pid and is_process_running(pid):
                log_for_debugging(f"[ScheduledTasks] scheduler lock held (PID {pid})")
                return False
        except OSError:
            return False
        try:
            path.unlink()
        except OSError:
            pass
        return await try_acquire_scheduler_lock(opts)
    except OSError as e:
        if get_errno_code(e) == "ENOENT":
            path.parent.mkdir(parents=True, exist_ok=True)
            return await try_acquire_scheduler_lock(opts)
        log_for_debugging(str(e))
        return False


async def release_scheduler_lock(opts: dict[str, Any] | None = None) -> None:
    path = Path(_lock_path(opts.get("dir") if opts else None))
    sid = _session_id(opts or {})
    try:
        raw = path.read_text(encoding="utf-8")
        data = safe_parse_json(raw)
        if isinstance(data, dict) and data.get("sessionId") == sid:
            path.unlink(missing_ok=True)
            log_for_debugging("[ScheduledTasks] released scheduler lock")
    except OSError:
        pass
