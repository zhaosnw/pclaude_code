"""Session lifecycle state and listeners (port of sessionState.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from hare.utils.env_utils import is_env_truthy
from hare.utils.sdk_event_queue import enqueue_sdk_event

SessionState = Literal["idle", "running", "requires_action"]


@dataclass
class RequiresActionDetails:
    tool_name: str
    action_description: str
    tool_use_id: str
    request_id: str
    input: dict[str, Any] | None = None


@dataclass
class SessionExternalMetadata:
    permission_mode: str | None = None
    is_ultraplan_mode: bool | None = None
    model: str | None = None
    pending_action: RequiresActionDetails | None = None
    post_turn_summary: Any = None
    task_summary: str | None = None


SessionStateChangedListener = Callable[
    [SessionState, RequiresActionDetails | None], None
]
SessionMetadataChangedListener = Callable[[SessionExternalMetadata], None]
PermissionModeChangedListener = Callable[[str], None]

_state_listener: SessionStateChangedListener | None = None
_metadata_listener: SessionMetadataChangedListener | None = None
_permission_mode_listener: PermissionModeChangedListener | None = None

_has_pending_action: bool = False
_current_state: SessionState = "idle"


def set_session_state_changed_listener(
    cb: SessionStateChangedListener | None,
) -> None:
    global _state_listener
    _state_listener = cb


def set_session_metadata_changed_listener(
    cb: SessionMetadataChangedListener | None,
) -> None:
    global _metadata_listener
    _metadata_listener = cb


def set_permission_mode_changed_listener(
    cb: PermissionModeChangedListener | None,
) -> None:
    global _permission_mode_listener
    _permission_mode_listener = cb


def get_session_state() -> SessionState:
    return _current_state


def notify_session_state_changed(
    state: SessionState,
    details: RequiresActionDetails | None = None,
) -> None:
    global _current_state, _has_pending_action
    _current_state = state
    if _state_listener:
        _state_listener(state, details)

    if state == "requires_action" and details is not None:
        _has_pending_action = True
        if _metadata_listener:
            _metadata_listener(SessionExternalMetadata(pending_action=details))
    elif _has_pending_action:
        _has_pending_action = False
        if _metadata_listener:
            _metadata_listener(SessionExternalMetadata(pending_action=None))

    if state == "idle" and _metadata_listener:
        _metadata_listener(SessionExternalMetadata(task_summary=None))

    import os

    if is_env_truthy(os.environ.get("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS")):
        enqueue_sdk_event(
            {
                "type": "system",
                "subtype": "session_state_changed",
                "state": state,
            }
        )


def notify_session_metadata_changed(metadata: SessionExternalMetadata) -> None:
    if _metadata_listener:
        _metadata_listener(metadata)


def notify_permission_mode_changed(mode: str) -> None:
    if _permission_mode_listener:
        _permission_mode_listener(mode)
