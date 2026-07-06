"""Global listener for command start/complete (mirrors `commandLifecycle.ts`)."""

from __future__ import annotations

from collections.abc import Callable

CommandLifecycleState = str  # 'started' | 'completed'

_listener: Callable[[str, CommandLifecycleState], None] | None = None


def set_command_lifecycle_listener(
    cb: Callable[[str, CommandLifecycleState], None] | None,
) -> None:
    global _listener
    _listener = cb


def notify_command_lifecycle(uuid: str, state: CommandLifecycleState) -> None:
    if _listener is not None:
        _listener(uuid, state)
