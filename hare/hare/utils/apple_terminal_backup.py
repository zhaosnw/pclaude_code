"""Apple Terminal.app preferences backup/restore (`appleTerminalBackup.ts`)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

from hare.utils.exec_file_no_throw import exec_file_no_throw
from hare.utils.log import log_error

_state: dict[str, Any] = {"in_progress": False, "backup_path": None}


def _state_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".hare", "apple_terminal_state.json")


def _load_state() -> None:
    global _state
    try:
        with open(_state_path(), encoding="utf-8") as f:
            _state.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass


def _save_state() -> None:
    os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(_state, f)


def mark_terminal_setup_in_progress(backup_path: str) -> None:
    _load_state()
    _state["in_progress"] = True
    _state["backup_path"] = backup_path
    _save_state()


def mark_terminal_setup_complete() -> None:
    _load_state()
    _state["in_progress"] = False
    _save_state()


def _get_terminal_recovery_info() -> tuple[bool, str | None]:
    _load_state()
    return bool(_state.get("in_progress")), _state.get("backup_path")


def get_terminal_plist_path() -> str:
    return str(Path.home() / "Library" / "Preferences" / "com.apple.Terminal.plist")


async def backup_terminal_preferences() -> str | None:
    terminal_plist_path = get_terminal_plist_path()
    backup_path = f"{terminal_plist_path}.bak"
    try:
        code, _, _ = await exec_file_no_throw(
            "defaults", ["export", "com.apple.Terminal", terminal_plist_path]
        )
        if code != 0:
            return None
        if not os.path.isfile(terminal_plist_path):
            return None
        await exec_file_no_throw(
            "defaults", ["export", "com.apple.Terminal", backup_path]
        )
        mark_terminal_setup_in_progress(backup_path)
        return backup_path
    except Exception as e:
        log_error(e)
        return None


class RestoreRestored(TypedDict):
    status: Literal["restored", "no_backup"]


class RestoreFailed(TypedDict):
    status: Literal["failed"]
    backup_path: str


RestoreResult = RestoreRestored | RestoreFailed


async def check_and_restore_terminal_backup() -> RestoreResult:
    in_progress, backup_path = _get_terminal_recovery_info()
    if not in_progress:
        return {"status": "no_backup"}
    if not backup_path:
        mark_terminal_setup_complete()
        return {"status": "no_backup"}
    if not os.path.isfile(backup_path):
        mark_terminal_setup_complete()
        return {"status": "no_backup"}
    try:
        code, _, _ = await exec_file_no_throw(
            "defaults", ["import", "com.apple.Terminal", backup_path]
        )
        if code != 0:
            return {"status": "failed", "backup_path": backup_path}
        await exec_file_no_throw("killall", ["cfprefsd"])
        mark_terminal_setup_complete()
        return {"status": "restored"}
    except Exception as e:
        log_error(RuntimeError(f"Failed to restore Terminal.app settings: {e}"))
        mark_terminal_setup_complete()
        return {"status": "failed", "backup_path": backup_path}
