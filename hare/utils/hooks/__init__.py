"""
Hook system for pre/post tool execution and turn lifecycle events.

Port of: src/utils/hooks/

Wires together the hook registry, shell execution, and JSON output parsing
to provide working implementations of the hook executors consumed by
query/stopHooks.ts.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional

from hare.utils.hooks.file_changed_watcher import FileChangedWatcher
from hare.utils.hooks.hook_events import HOOK_EVENTS, HookEvent
from hare.utils.hooks.hook_registry import AsyncHookRegistry, get_hook_registry
from hare.utils.hooks.exec_hook import exec_hook
from hare.utils.hooks.hook_helpers import (
    normalize_hook_json_output,
    resolve_hook_decision,
    interpret_hook_exit_code,
)

# Default timeout for hook execution (10 minutes, matching TS)
TOOL_HOOK_EXECUTION_TIMEOUT_MS = 600_000


@dataclass
class HookBlockingError:
    blocking_error: str
    command: str = ""


def _get_matching_hooks(event: HookEvent) -> list[Any]:
    """Get registered hooks matching an event."""
    registry = get_hook_registry()
    return registry.get_handlers(event)


async def _run_single_hook(
    handler: Any,
    context: dict[str, Any],
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> dict[str, Any]:
    """Run a single hook and parse its output with full protocol support.

    TS: hooks.ts hook execution — supports:
    - Exit code semantics (0=pass, 2=block, other=warn)
    - JSON response protocol (decision, updatedInput, additionalContext, continue, hookSpecificOutput)
    - Multi-type dispatch (command/prompt/agent/http)

    Returns dict with: message, blockingError, preventContinuation, stopReason,
    updatedInput, additionalContext, permissionDecision, etc.
    """
    hook_type = getattr(handler, "type", "command")
    command = getattr(handler, "name", "") or getattr(handler, "command", "")

    timeout_sec = timeout_ms / 1000.0

    # Dispatch by hook type (TS: supports command/prompt/agent/http)
    try:
        if hook_type == "prompt":
            result = await _exec_prompt_hook(handler, context, timeout_sec)
        elif hook_type == "agent":
            result = await _exec_agent_hook(handler, context, timeout_sec)
        elif hook_type == "http":
            result = await _exec_http_hook(handler, context, timeout_sec)
        else:
            # Default: command hook
            if not command:
                return {}
            result = await exec_hook(command, timeout=timeout_sec)
    except Exception as e:
        return {
            "blockingError": HookBlockingError(
                blocking_error=f"Hook '{command}' failed: {e}",
                command=command,
            ),
        }

    exit_code = result.get("exit_code", 0)
    success = result.get("success", exit_code == 0)
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    # Parse JSON output from hook stdout
    parsed = normalize_hook_json_output(stdout) if stdout.strip() else None

    # Interpret exit code + JSON together (TS exit code semantics)
    interpretation = interpret_hook_exit_code(exit_code, parsed)

    response: dict[str, Any] = {}

    if interpretation["action"] == "block":
        error_msg = interpretation["reason"] or stderr or f"Hook exit code {exit_code}"
        response["blockingError"] = HookBlockingError(
            blocking_error=error_msg,
            command=command,
        )
        return response

    if interpretation["action"] == "warn":
        # Warn but don't block — log stderr to console
        if stderr.strip():
            response["warning"] = stderr.strip()

    if not success and parsed is None:
        error_msg = stderr or result.get("error") or f"Exit code {exit_code}"
        if exit_code != 0:
            response["blockingError"] = HookBlockingError(
                blocking_error=error_msg,
                command=command,
            )
            return response
        return {}

    if parsed is None:
        return response

    # Resolve hook decision from parsed JSON (TS hook response protocol)
    resolved = resolve_hook_decision(parsed)
    response.update(resolved)

    return response


async def _exec_prompt_hook(
    handler: Any, context: dict[str, Any], timeout_sec: float
) -> dict[str, Any]:
    """Execute a Prompt-type hook (LLM evaluation). Stub — not yet wired."""
    from hare.utils.hooks.exec_prompt_hook import exec_prompt_hook, PromptHookOutcome

    prompt = getattr(handler, "prompt", "")
    try:
        outcome: PromptHookOutcome = await exec_prompt_hook(prompt)
        if outcome.stdout:
            return {
                "success": True,
                "stdout": outcome.stdout,
                "exit_code": 0,
                "stderr": "",
            }
        return {
            "success": True,
            "stdout": outcome.rendered,
            "exit_code": 0,
            "stderr": "",
        }
    except Exception as e:
        return {"success": False, "stdout": "", "exit_code": 1, "stderr": str(e)}


async def _exec_agent_hook(
    handler: Any, context: dict[str, Any], timeout_sec: float
) -> dict[str, Any]:
    """Execute an Agent-type hook (multi-step LLM validation). Stub — not yet wired."""
    from hare.utils.hooks.exec_agent_hook import exec_agent_hook

    try:
        outcome = await exec_agent_hook(getattr(handler, "prompt", ""))
        return {
            "success": True,
            "stdout": outcome.stdout,
            "exit_code": 0,
            "stderr": outcome.stderr,
        }
    except Exception as e:
        return {"success": False, "stdout": "", "exit_code": 1, "stderr": str(e)}


async def _exec_http_hook(
    handler: Any, context: dict[str, Any], timeout_sec: float
) -> dict[str, Any]:
    """Execute an HTTP-type hook (POST to external service). Stub — not yet wired."""
    from hare.utils.hooks.exec_http_hook import exec_http_hook

    try:
        url = getattr(handler, "url", "")
        headers = getattr(handler, "headers", {})
        result = await exec_http_hook(url, json=context, headers=headers)
        return {
            "success": result.status_code < 400,
            "stdout": result.body,
            "exit_code": 0 if result.status_code < 400 else 1,
            "stderr": "",
        }
    except Exception as e:
        return {"success": False, "stdout": "", "exit_code": 1, "stderr": str(e)}


async def execute_stop_hooks(
    permission_mode: Any = None,
    abort_signal: Any = None,
    request_id: Optional[str] = None,
    stop_hook_active: bool = False,
    agent_id: Optional[str] = None,
    tool_use_context: Any = None,
    messages: list[Any] | None = None,
    agent_type: Optional[str] = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute Stop / SubagentStop hooks.

    Yields {message, blockingError, preventContinuation, stopReason} dicts.
    """
    event: HookEvent = "SubagentStop" if agent_id else "Stop"
    handlers = _get_matching_hooks(event)
    if not handlers:
        return

    # Check abort before running hooks
    if abort_signal is not None and getattr(abort_signal, "aborted", False):
        return

    # Run hooks concurrently
    tasks = []
    for handler in handlers:
        context = {
            "permission_mode": permission_mode,
            "tool_use_context": tool_use_context,
            "messages": messages or [],
            "agent_id": agent_id,
        }
        tasks.append(_run_single_hook(handler, context))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, dict) and r:
            yield r


async def execute_task_completed_hooks(
    task_id: str = "",
    subject: str = "",
    description: str = "",
    teammate_name: str = "",
    team_name: str = "",
    permission_mode: Any = None,
    abort_signal: Any = None,
    request_id: Optional[str] = None,
    tool_use_context: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute TaskCompleted hooks for tasks owned by this teammate."""
    event: HookEvent = "Notification"
    handlers = _get_matching_hooks(event)
    if not handlers:
        return

    if abort_signal is not None and getattr(abort_signal, "aborted", False):
        return

    tasks = []
    for handler in handlers:
        context = {
            "task_id": task_id,
            "subject": subject,
            "description": description,
            "teammate_name": teammate_name,
            "team_name": team_name,
            "permission_mode": permission_mode,
            "tool_use_context": tool_use_context,
        }
        tasks.append(_run_single_hook(handler, context))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, dict) and r:
            yield r


async def execute_teammate_idle_hooks(
    teammate_name: str = "",
    team_name: str = "",
    permission_mode: Any = None,
    abort_signal: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute TeammateIdle hooks."""
    event: HookEvent = "Notification"
    handlers = _get_matching_hooks(event)
    if not handlers:
        return

    tasks = []
    for handler in handlers:
        context = {
            "teammate_name": teammate_name,
            "team_name": team_name,
            "permission_mode": permission_mode,
        }
        tasks.append(_run_single_hook(handler, context))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, dict) and r:
            yield r


async def execute_stop_failure_hooks(*args: Any, **kwargs: Any) -> None:
    """Execute StopFailure hooks — fire-and-forget, no return value.

    Mirrors TS executeStopFailureHooks: extracts text from lastMessage,
    runs hooksOutsideREPL pattern. Output goes to logs only.
    """
    if not args:
        return
    last_message = args[0]
    tool_use_context = args[1] if len(args) > 1 else kwargs.get("tool_use_context")

    event: HookEvent = "Stop"
    handlers = _get_matching_hooks(event)
    if not handlers:
        return

    # Extract error text from last message
    error_text = ""
    if hasattr(last_message, "api_error"):
        error_text = str(last_message.api_error or "")
    elif isinstance(last_message, dict):
        error_text = str(last_message.get("api_error", ""))
    if not error_text:
        error_text = "unknown"

    async def _fire_and_forget() -> None:
        for handler in handlers:
            try:
                await _run_single_hook(
                    handler,
                    {
                        "error": error_text,
                        "tool_use_context": tool_use_context,
                    },
                )
            except Exception:
                pass

    asyncio.ensure_future(_fire_and_forget())


def get_stop_hook_message(blocking_error: Any) -> str:
    """Format a stop hook blocking error for display."""
    if isinstance(blocking_error, HookBlockingError):
        return f"Stop hook blocked: {blocking_error.blocking_error}"
    if hasattr(blocking_error, "blockingError"):
        return str(blocking_error.blockingError)
    return str(blocking_error)


def get_task_completed_hook_message(blocking_error: Any) -> str:
    """Format a task completed hook blocking error for display."""
    if isinstance(blocking_error, HookBlockingError):
        return f"TaskCompleted hook blocked: {blocking_error.blocking_error}"
    if hasattr(blocking_error, "blockingError"):
        return str(blocking_error.blockingError)
    return str(blocking_error)


def get_teammate_idle_hook_message(blocking_error: Any) -> str:
    """Format a teammate idle hook blocking error for display."""
    if isinstance(blocking_error, HookBlockingError):
        return f"TeammateIdle hook blocked: {blocking_error.blocking_error}"
    if hasattr(blocking_error, "blockingError"):
        return str(blocking_error.blockingError)
    return str(blocking_error)
