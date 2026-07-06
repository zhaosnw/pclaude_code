"""Port of: src/state/selectors.ts"""

from __future__ import annotations
from typing import Any
from hare.state.app_state import AppState, get_app_state


def select_messages(state: AppState | None = None) -> list[dict[str, Any]]:
    return (state or get_app_state()).messages


def select_model(state: AppState | None = None) -> str:
    return (state or get_app_state()).model


def select_permission_mode(state: AppState | None = None) -> str:
    return (state or get_app_state()).permission_mode


def select_is_processing(state: AppState | None = None) -> bool:
    return (state or get_app_state()).is_processing


def select_session_id(state: AppState | None = None) -> str:
    return (state or get_app_state()).session_id
