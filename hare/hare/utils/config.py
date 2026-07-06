"""
Configuration management.

Port of: src/utils/config.ts
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from hare.utils.env_utils import get_hare_config_home_dir


@dataclass
class GlobalConfig:
    theme: str = "default"
    last_release_notes_seen: Optional[str] = None
    # Optional fields used by buddy/companion and OAuth (extended settings)
    user_id: Optional[str] = None
    oauth_account: Optional[Any] = None
    companion: Optional[Any] = None
    companion_muted: bool = False
    auto_compact_enabled: bool = True


@dataclass
class ProjectConfig:
    last_cost: Optional[float] = None
    last_duration: Optional[float] = None
    last_api_duration: Optional[float] = None
    last_tool_duration: Optional[float] = None
    last_session_id: Optional[str] = None
    last_lines_added: Optional[int] = None
    last_lines_removed: Optional[int] = None


def _get_config_dir() -> str:
    return get_hare_config_home_dir()


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def get_global_config() -> GlobalConfig:
    data = _load_json(os.path.join(_get_config_dir(), "config.json"))
    return GlobalConfig(
        theme=data.get("theme", "default"),
        last_release_notes_seen=data.get("lastReleaseNotesSeen"),
        user_id=data.get("userID"),
        oauth_account=data.get("oauthAccount"),
        companion=data.get("companion"),
        companion_muted=bool(data.get("companionMuted", False)),
    )


@lru_cache(maxsize=1)
def get_current_project_config() -> ProjectConfig:
    data = _load_json(os.path.join(_get_config_dir(), "project.json"))
    return ProjectConfig(
        last_cost=data.get("lastCost"),
        last_duration=data.get("lastDuration"),
    )


def reload_config_snapshots() -> None:
    """Explicitly clear local config snapshots so subsequent reads reload from disk."""
    get_global_config.cache_clear()
    get_current_project_config.cache_clear()


def enable_configs() -> None:
    """Compatibility no-op for open-source/local mode."""
    reload_config_snapshots()


def save_current_project_config(config: dict[str, Any]) -> None:
    """Save the current project config (P2 — stub)."""
    _ = config
    pass
