"""Port of: src/utils/sessionActivity.ts"""

from __future__ import annotations

_active = False


def is_session_activity_tracking_active() -> bool:
    return _active


def send_session_activity_signal() -> None:
    pass


def start_session_activity_tracking() -> None:
    global _active
    _active = True


def stop_session_activity_tracking() -> None:
    global _active
    _active = False
