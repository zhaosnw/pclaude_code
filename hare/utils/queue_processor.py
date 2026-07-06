"""
Drain the REPL message queue. Port of src/utils/queueProcessor.ts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from hare.utils.message_queue_manager import (
    dequeue,
    dequeue_all_matching,
    has_commands_in_queue,
    peek,
)


class ProcessQueueParams(TypedDict):
    execute_input: Callable[[list[Any]], Awaitable[None]]


class ProcessQueueResult(TypedDict):
    processed: bool


def _is_slash_command(cmd: Any) -> bool:
    val = getattr(cmd, "value", cmd)
    if isinstance(val, str):
        return val.strip().startswith("/")
    if isinstance(val, list):
        for block in val:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", "")).strip().startswith("/")
    return False


def process_queue_if_ready(params: ProcessQueueParams) -> ProcessQueueResult:
    execute_input = params["execute_input"]

    def is_main_thread(cmd: Any) -> bool:
        return getattr(cmd, "agent_id", None) is None

    nxt = peek(is_main_thread)
    if not nxt:
        return {"processed": False}

    if _is_slash_command(nxt) or getattr(nxt, "mode", None) == "bash":
        cmd = dequeue(is_main_thread)
        if cmd is not None:
            import asyncio

            asyncio.create_task(execute_input([cmd]))
        return {"processed": True}

    target_mode = getattr(nxt, "mode", None)
    commands = dequeue_all_matching(
        lambda c: is_main_thread(c)
        and not _is_slash_command(c)
        and getattr(c, "mode", None) == target_mode
    )
    if not commands:
        return {"processed": False}
    import asyncio

    asyncio.create_task(execute_input(commands))
    return {"processed": True}


def has_queued_commands() -> bool:
    return has_commands_in_queue()
