"""iTerm2 plist backup/restore — port of `iTermBackup.ts`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def _global_config_path() -> Path:
    return Path.home() / ".hare.json"


def _load() -> dict:
    p = _global_config_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    p = _global_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def mark_iterm2_setup_complete() -> None:
    cur = _load()
    cur["iterm2SetupInProgress"] = False
    _save(cur)


def _recovery_info() -> tuple[bool, str | None]:
    cfg = _load()
    return (bool(cfg.get("iterm2SetupInProgress")), cfg.get("iterm2BackupPath"))


def _iterm2_plist_path() -> str:
    return str(Path.home() / "Library" / "Preferences" / "com.googlecode.iterm2.plist")


async def check_and_restore_iterm_backup() -> dict[str, str]:
    in_progress, backup_path = _recovery_info()
    if not in_progress:
        return {"status": "no_backup"}
    if not backup_path:
        mark_iterm2_setup_complete()
        return {"status": "no_backup"}
    bp = Path(backup_path)
    if not bp.is_file():
        mark_iterm2_setup_complete()
        return {"status": "no_backup"}
    try:
        shutil.copy2(bp, _iterm2_plist_path())
        mark_iterm2_setup_complete()
        return {"status": "restored"}
    except OSError:
        mark_iterm2_setup_complete()
        return {"status": "failed", "backupPath": backup_path}
