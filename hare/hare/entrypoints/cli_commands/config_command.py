"""
CLI config command – manage configuration.

Port of: src/entrypoints/cli/configCommand.ts
"""

from __future__ import annotations

import json
import os
from typing import Any

from hare.utils.config import reload_config_snapshots
from hare.utils.config_full import reload_global_config
from hare.utils.global_claude_json import reload_global_hare_json
from hare.utils.settings.settings import reload_settings


def get_config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".hare", "config.json")


async def run_config_get(key: str) -> Any:
    """Get a config value."""
    config = _load_config()
    parts = key.split(".")
    current: Any = config
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


async def run_config_set(key: str, value: str) -> None:
    """Set a config value."""
    config = _load_config()
    parts = key.split(".")
    current = config
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    try:
        current[parts[-1]] = json.loads(value)
    except json.JSONDecodeError:
        current[parts[-1]] = value
    _save_config(config)


async def run_config_reload(project_dir: str = "") -> dict[str, Any]:
    """Explicitly reload config/settings snapshots from disk."""
    reload_config_snapshots()
    global_config = reload_global_config()
    global_json = reload_global_hare_json()
    settings = reload_settings(project_dir=project_dir)
    return {
        "global_config": global_config,
        "global_json": global_json,
        "settings": settings,
    }


def _load_config() -> dict[str, Any]:
    path = get_config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(config: dict[str, Any]) -> None:
    path = get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
