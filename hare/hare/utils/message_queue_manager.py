"""Global command queue for prompt/bash/notifications — port of `messageQueueManager.ts`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from hare.utils.signal import create_signal

QueuePriority = Literal["now", "next", "later"]


@dataclass
class QueuedCommand:
    value: str | list[dict[str, Any]]
    mode: str = "prompt"
    priority: QueuePriority = "next"
    pre_expansion_value: str | None = None
    pasted_contents: dict[int, Any] | None = None
    skip_slash_commands: bool = False
    uuid: str | None = None
    is_meta: bool = False
    origin: Any | None = None


PRIORITY_ORDER = {"now": 0, "next": 1, "later": 2}

_command_queue: list[QueuedCommand] = []
_snapshot: tuple[QueuedCommand, ...] = ()
_changed = create_signal()


def _notify() -> None:
    global _snapshot
    _snapshot = tuple(_command_queue)
    _changed.emit()


def subscribe_to_command_queue(cb: Callable[[], None]) -> Callable[[], None]:
    return _changed.subscribe(cb)


def get_command_queue_snapshot() -> tuple[QueuedCommand, ...]:
    return _snapshot


def get_command_queue() -> list[QueuedCommand]:
    return list(_command_queue)


def get_command_queue_length() -> int:
    return len(_command_queue)


def has_commands_in_queue() -> bool:
    return len(_command_queue) > 0


def recheck_command_queue() -> None:
    if _command_queue:
        _notify()


def enqueue(command: QueuedCommand) -> None:
    if command.priority is None:
        command.priority = "next"
    _command_queue.append(command)
    _notify()


def dequeue(
    filter_fn: Callable[[QueuedCommand], bool] | None = None,
) -> QueuedCommand | None:
    if not _command_queue:
        return None
    best_idx = -1
    best_pri = 10**9
    for i, cmd in enumerate(_command_queue):
        if filter_fn and not filter_fn(cmd):
            continue
        p = PRIORITY_ORDER.get(cmd.priority or "next", 1)
        if p < best_pri:
            best_pri = p
            best_idx = i
    if best_idx < 0:
        return None
    cmd = _command_queue.pop(best_idx)
    _notify()
    return cmd


def dequeue_all() -> list[QueuedCommand]:
    if not _command_queue:
        return []
    out = list(_command_queue)
    _command_queue.clear()
    _notify()
    return out


def clear_command_queue() -> None:
    if not _command_queue:
        return
    _command_queue.clear()
    _notify()


def reset_command_queue() -> None:
    global _snapshot
    _command_queue.clear()
    _snapshot = tuple()


def is_prompt_input_mode_editable(mode: str) -> bool:
    return mode != "task-notification"


def is_queued_command_editable(cmd: QueuedCommand) -> bool:
    return is_prompt_input_mode_editable(cmd.mode) and not cmd.is_meta


def is_slash_command(cmd: QueuedCommand) -> bool:
    if cmd.skip_slash_commands:
        return False
    if isinstance(cmd.value, str):
        return cmd.value.strip().startswith("/")
    return False


def peek(
    filter_fn: Callable[[QueuedCommand], bool] | None = None,
) -> QueuedCommand | None:
    """Peek at the next command without removing it."""
    if not _command_queue:
        return None
    best_idx = -1
    best_pri = 10**9
    for i, cmd in enumerate(_command_queue):
        if filter_fn and not filter_fn(cmd):
            continue
        p = PRIORITY_ORDER.get(cmd.priority or "next", 1)
        if p < best_pri:
            best_pri = p
            best_idx = i
    if best_idx < 0:
        return None
    return _command_queue[best_idx]


def dequeue_all_matching(
    filter_fn: Callable[[QueuedCommand], bool],
) -> list[QueuedCommand]:
    """Dequeue all commands matching the filter function."""
    matched = [cmd for cmd in _command_queue if filter_fn(cmd)]
    for cmd in matched:
        _command_queue.remove(cmd)
    if matched:
        _notify()
    return matched


_notify()
