"""
Team file management helpers.

Port of: src/utils/swarm/teamHelpers.ts
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TEAMS_DIR = Path.home() / ".hare" / "teams"
_cleanup_registry: set[str] = set()


def sanitize_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())


def get_team_file_path(team_name: str) -> str:
    return str(_TEAMS_DIR / team_name / "config.json")


def read_team_file(team_name: str) -> dict[str, Any] | None:
    path = _TEAMS_DIR / team_name / "config.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


async def write_team_file(team_name: str, data: dict[str, Any]) -> None:
    path = _TEAMS_DIR / team_name
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def register_team_for_session_cleanup(team_name: str) -> None:
    _cleanup_registry.add(team_name)


def unregister_team_for_session_cleanup(team_name: str) -> None:
    _cleanup_registry.discard(team_name)


async def cleanup_team_directories(team_name: str) -> None:
    import shutil

    team_path = _TEAMS_DIR / team_name
    tasks_path = Path.home() / ".hare" / "tasks" / team_name
    if team_path.exists():
        shutil.rmtree(team_path, ignore_errors=True)
    if tasks_path.exists():
        shutil.rmtree(tasks_path, ignore_errors=True)
