"""PID registry for concurrent CLI sessions (`concurrentSessions.ts`)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from hare.utils.cleanup_registry import register_cleanup
from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.errors import error_message, is_fs_inaccessible
from hare.utils.generic_process_utils import is_process_running
from hare.utils.platform import get_platform

SessionKind = Literal["interactive", "bg", "daemon", "daemon-worker"]
SessionStatus = Literal["busy", "idle", "waiting"]


def _sessions_dir() -> str:
    return str(Path(get_hare_config_home_dir()) / "sessions")


def _env_session_kind() -> SessionKind | None:
    k = os.environ.get("CLAUDE_CODE_SESSION_KIND")
    if k in ("bg", "daemon", "daemon-worker"):
        return k  # type: ignore[return-value]
    return None


def is_bg_session() -> bool:
    return _env_session_kind() == "bg"


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _json_loads(s: str) -> Any:
    return json.loads(s)


def _agent_id() -> str | None:
    return os.environ.get("CLAUDE_CODE_AGENT")


async def register_session() -> bool:
    if _agent_id() is not None:
        return False
    kind: SessionKind = _env_session_kind() or "interactive"
    d = Path(_sessions_dir())
    pid_file = d / f"{os.getpid()}.json"

    async def _unlink() -> None:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    register_cleanup(_unlink)

    try:
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        payload = {
            "pid": os.getpid(),
            "sessionId": _get_session_id_stub(),
            "cwd": os.getcwd(),
            "startedAt": __import__("time").time() * 1000,
            "kind": kind,
            "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT"),
        }
        pid_file.write_text(_json_dumps(payload), encoding="utf-8")
        return True
    except OSError as e:
        log_for_debugging(f"[concurrentSessions] register failed: {error_message(e)}")
        return False


def _get_session_id_stub() -> str:
    return os.environ.get("CLAUDE_SESSION_ID", "local-session")


async def update_session_name(name: str | None) -> None:
    if not name:
        return
    await _update_pid_file({"name": name})


async def update_session_bridge_id(bridge_session_id: str | None) -> None:
    await _update_pid_file({"bridgeSessionId": bridge_session_id})


async def update_session_activity(patch: dict[str, Any]) -> None:
    if os.environ.get("CLAUDE_CODE_BG_SESSIONS", "") != "1":
        return
    patch = {**patch, "updatedAt": int(__import__("time").time() * 1000)}
    await _update_pid_file(patch)


async def _update_pid_file(patch: dict[str, Any]) -> None:
    pid_file = Path(_sessions_dir()) / f"{os.getpid()}.json"
    try:
        data = _json_loads(pid_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        data.update(patch)
        pid_file.write_text(_json_dumps(data), encoding="utf-8")
    except OSError as e:
        log_for_debugging(
            f"[concurrentSessions] updatePidFile failed: {error_message(e)}"
        )


async def count_concurrent_sessions() -> int:
    d = Path(_sessions_dir())
    try:
        files = list(d.iterdir())
    except OSError as e:
        if not is_fs_inaccessible(e):
            log_for_debugging(
                f"[concurrentSessions] readdir failed: {error_message(e)}"
            )
        return 0

    count = 0
    for f in files:
        if not re.fullmatch(r"\d+\.json", f.name):
            continue
        pid = int(f.name[:-5])
        if pid == os.getpid():
            count += 1
            continue
        if is_process_running(pid):
            count += 1
        elif get_platform() != "wsl":
            try:
                f.unlink()
            except OSError:
                pass
    return count
