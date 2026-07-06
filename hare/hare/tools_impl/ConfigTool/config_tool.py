"""
ConfigTool – get/set configuration values.

Port of: src/tools/ConfigTool/ConfigTool.ts
"""

from __future__ import annotations
import json
import os
from typing import Any

TOOL_NAME = "Config"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "setting": {"type": "string", "description": "Setting key (dot notation)"},
            "value": {"type": "string", "description": "New value (omit for GET)"},
        },
        "required": ["setting"],
    }


def _get_config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".hare", "config.json")


def _load_config() -> dict[str, Any]:
    path = _get_config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(config: dict[str, Any]) -> None:
    path = _get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


async def call(setting: str, value: str | None = None, **kwargs: Any) -> dict[str, Any]:
    config = _load_config()
    parts = setting.split(".")
    if value is None:
        current: Any = config
        for p in parts:
            if isinstance(current, dict):
                current = current.get(p)
            else:
                return {"data": "null"}
        return {"data": json.dumps(current)}
    current_dict = config
    for p in parts[:-1]:
        if p not in current_dict or not isinstance(current_dict[p], dict):
            current_dict[p] = {}
        current_dict = current_dict[p]
    try:
        current_dict[parts[-1]] = json.loads(value)
    except json.JSONDecodeError:
        current_dict[parts[-1]] = value
    _save_config(config)
    return {"data": f"Set {setting} = {value}"}
