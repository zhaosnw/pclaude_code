"""
Registry for pending async hook subprocesses.

Port of: src/utils/hooks/AsyncHookRegistry.ts
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

HookEvent = str
Outcome = Literal["success", "error", "cancelled"]


def _log_debug(_msg: str, **_kw: Any) -> None:
    pass


def emit_hook_response(_payload: dict[str, Any]) -> None:
    """Stub for hookEvents.emitHookResponse."""
    pass


def start_hook_progress_interval(**_kwargs: Any) -> Callable[[], None]:
    """Stub for hookEvents.startHookProgressInterval."""
    return lambda: None


def _invalidate_session_env_cache() -> None:
    pass


class _TaskOutput(Protocol):
    async def get_stdout(self) -> str: ...
    def get_stderr(self) -> str: ...


class _ShellCommand(Protocol):
    status: str
    task_output: _TaskOutput | None
    result: asyncio.Future[Any]

    def cleanup(self) -> None: ...
    def kill(self) -> None: ...


@dataclass
class PendingAsyncHook:
    process_id: str
    hook_id: str
    hook_name: str
    hook_event: HookEvent
    tool_name: str | None = None
    plugin_id: str | None = None
    start_time: float = field(default_factory=time.time)
    timeout: float = 15_000.0
    command: str = ""
    response_attachment_sent: bool = False
    shell_command: _ShellCommand | None = None
    stop_progress_interval: Callable[[], None] = field(default=lambda: None)


_pending_hooks: dict[str, PendingAsyncHook] = {}


def register_pending_async_hook(
    *,
    process_id: str,
    hook_id: str,
    async_response: dict[str, Any],
    hook_name: str,
    hook_event: HookEvent,
    command: str,
    shell_command: _ShellCommand,
    tool_name: str | None = None,
    plugin_id: str | None = None,
) -> None:
    timeout = float(
        async_response.get("asyncTimeout")
        or async_response.get("async_timeout")
        or 15_000
    )

    async def get_output() -> dict[str, str]:
        task_output = shell_command.task_output
        if not task_output:
            return {"stdout": "", "stderr": "", "output": ""}
        stdout = await task_output.get_stdout()
        stderr = task_output.get_stderr()
        return {"stdout": stdout, "stderr": stderr, "output": stdout + stderr}

    stop = start_hook_progress_interval(
        hook_id=hook_id,
        hook_name=hook_name,
        hook_event=hook_event,
        get_output=get_output,
    )
    _log_debug(
        f"Hooks: Registering async hook {process_id} ({hook_name}) with timeout {timeout}ms"
    )
    _pending_hooks[process_id] = PendingAsyncHook(
        process_id=process_id,
        hook_id=hook_id,
        hook_name=hook_name,
        hook_event=hook_event,
        tool_name=tool_name,
        plugin_id=plugin_id,
        command=command,
        timeout=timeout,
        shell_command=shell_command,
        stop_progress_interval=stop,
    )


def get_pending_async_hooks() -> list[PendingAsyncHook]:
    return [h for h in _pending_hooks.values() if not h.response_attachment_sent]


async def _finalize_hook(
    hook: PendingAsyncHook, exit_code: int, outcome: Outcome
) -> None:
    hook.stop_progress_interval()
    task_output = hook.shell_command.task_output if hook.shell_command else None
    stdout = await task_output.get_stdout() if task_output else ""
    stderr = task_output.get_stderr() if task_output else ""
    if hook.shell_command:
        hook.shell_command.cleanup()
    emit_hook_response(
        {
            "hookId": hook.hook_id,
            "hookName": hook.hook_name,
            "hookEvent": hook.hook_event,
            "output": stdout + stderr,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
            "outcome": outcome,
        }
    )


async def _process_one_hook(hook: PendingAsyncHook) -> dict[str, Any]:
    if not hook.shell_command:
        hook.stop_progress_interval()
        return {"type": "remove", "process_id": hook.process_id}
    sc = hook.shell_command
    if sc.status == "killed":
        hook.stop_progress_interval()
        sc.cleanup()
        return {"type": "remove", "process_id": hook.process_id}
    if sc.status != "completed":
        return {"type": "skip"}
    to = sc.task_output
    stdout = await to.get_stdout() if to else ""
    if hook.response_attachment_sent or not stdout.strip():
        hook.stop_progress_interval()
        return {"type": "remove", "process_id": hook.process_id}
    exec_result = await sc.result
    exit_code = int(getattr(exec_result, "code", 1))
    response: dict[str, Any] = {}
    for line in stdout.split("\n"):
        t = line.strip()
        if t.startswith("{"):
            try:
                parsed = json.loads(t)
                if "async" not in parsed:
                    response = parsed
                    break
            except json.JSONDecodeError:
                pass
    hook.response_attachment_sent = True
    await _finalize_hook(hook, exit_code, "success" if exit_code == 0 else "error")
    stderr = to.get_stderr() if to else ""
    return {
        "type": "response",
        "process_id": hook.process_id,
        "is_session_start": hook.hook_event == "SessionStart",
        "payload": {
            "processId": hook.process_id,
            "response": response,
            "hookName": hook.hook_name,
            "hookEvent": hook.hook_event,
            "toolName": hook.tool_name,
            "pluginId": hook.plugin_id,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
        },
    }


async def check_for_async_hook_responses() -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    hooks = list(_pending_hooks.values())
    settled = await asyncio.gather(
        *[_process_one_hook(h) for h in hooks],
        return_exceptions=True,
    )
    session_start_completed = False
    for s in settled:
        if isinstance(s, BaseException):
            _log_debug(f"Hooks: callback error: {s!r}")
            continue
        if s["type"] == "remove":
            _pending_hooks.pop(s["process_id"], None)
        elif s["type"] == "response":
            responses.append(s["payload"])
            _pending_hooks.pop(s["process_id"], None)
            if s.get("is_session_start"):
                session_start_completed = True
    if session_start_completed:
        _invalidate_session_env_cache()
    return responses


def remove_delivered_async_hooks(process_ids: list[str]) -> None:
    for pid in process_ids:
        hook = _pending_hooks.get(pid)
        if hook and hook.response_attachment_sent:
            hook.stop_progress_interval()
            _pending_hooks.pop(pid, None)


async def finalize_pending_async_hooks() -> None:
    for hook in list(_pending_hooks.values()):
        sc = hook.shell_command
        if sc and sc.status == "completed":
            result = await sc.result
            code = int(getattr(result, "code", 1))
            await _finalize_hook(hook, code, "success" if code == 0 else "error")
        else:
            if sc and sc.status != "killed":
                sc.kill()
            await _finalize_hook(hook, 1, "cancelled")
    _pending_hooks.clear()


def clear_all_async_hooks() -> None:
    for hook in _pending_hooks.values():
        hook.stop_progress_interval()
    _pending_hooks.clear()
