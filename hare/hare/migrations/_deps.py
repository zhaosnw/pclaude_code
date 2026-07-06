"""Stubs for migration dependencies (config, auth, settings, analytics)."""

from __future__ import annotations

import os
from typing import Any, Callable

_GLOBAL_CONFIG: dict[str, Any] = {}
_PROJECT_CONFIG: dict[str, Any] = {}


def log_event(_name: str, _payload: dict[str, Any] | None = None) -> None:
    pass


def get_global_config() -> dict[str, Any]:
    return dict(_GLOBAL_CONFIG)


def save_global_config(updater: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    global _GLOBAL_CONFIG
    _GLOBAL_CONFIG = updater(dict(_GLOBAL_CONFIG))


def get_current_project_config() -> dict[str, Any]:
    return dict(_PROJECT_CONFIG)


def save_current_project_config(
    updater: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    global _PROJECT_CONFIG
    _PROJECT_CONFIG = updater(dict(_PROJECT_CONFIG))


def get_settings_for_source(_source: str) -> dict[str, Any] | None:
    return None


def update_settings_for_source(_source: str, _patch: dict[str, Any]) -> None:
    pass


def get_api_provider() -> str:
    return os.environ.get("CLAUDE_API_PROVIDER", "firstParty")


def is_pro_subscriber() -> bool:
    return False


def is_max_subscriber() -> bool:
    return False


def is_team_premium_subscriber() -> bool:
    return False


def log_error(_err: BaseException) -> None:
    pass
