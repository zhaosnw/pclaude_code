"""Stop-hook orchestration after each query turn.

Port of: src/query/stopHooks.ts (473 lines TS, line-by-line).

Cross-language adaptations:
- `AsyncGenerator<Y, R>` → ``AsyncGenerator[Y, None]`` and a final ``return``
  expression. Python returns the value via ``StopAsyncIteration.value``,
  which mirrors the TS generator-return semantics exactly.
- ``feature('X')``  → ``hare.utils.bundle_feature.feature('X')``.
- ``await import(...)``  → ``importlib.import_module(...)`` inside the same
  conditional so the gated module isn't loaded eagerly.
- ``Promise.race([p, setTimeout(... ).unref()])`` → ``asyncio.wait_for`` with
  the same timeout (Python tasks don't keep the loop alive after completion
  the way Node timers do — no ``unref()`` needed).
- ``Date.now()`` → ``time.time() * 1000`` (millisecond floats).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional, Union

from hare.constants.query_source import QuerySource

# -- src/query/stopHooks.ts L1-3
from hare.utils.bundle_feature import feature
from hare.keybindings.shortcut_format import get_shortcut_display
from hare.memdir.paths import is_extract_mode_active

# -- L4-7
from hare.services.analytics import log_event

# -- L8
from hare.tool import ToolUseContext

# -- L9
from hare.app_types.hooks import HookProgress

# -- L10-18
from hare.app_types.message import (
    AssistantMessage,
    Message,
    RequestStartEvent,
    StopHookInfo,
    StreamEvent,
    TombstoneMessage,
    ToolUseSummaryMessage,
)

# -- L19
from hare.utils.attachments import create_attachment_message

# -- L20
from hare.utils.debug import log_for_debugging

# -- L21
from hare.utils.errors import error_message

# -- L22
from hare.utils.hooks.post_sampling_hooks import REPLHookContext

# -- L23-30
from hare.utils.hooks import (
    execute_stop_hooks,
    execute_task_completed_hooks,
    execute_teammate_idle_hooks,
    get_stop_hook_message,
    get_task_completed_hook_message,
    get_teammate_idle_hook_message,
)

# -- L31-36
from hare.utils.messages import (
    create_stop_hook_summary_message,
    create_system_message,
    create_user_interruption_message,
    create_user_message,
)

# -- L37
from hare.utils.system_prompt_type import SystemPrompt

# -- L38
from hare.utils.tasks import get_task_list_id, list_tasks

# -- L39
from hare.utils.teammate import get_agent_name, get_team_name, is_teammate


# -- L41-49 — gated module imports.
#
# TS compiles `feature('EXTRACT_MEMORIES') ? require(...) : null` to either
# the bound module reference (if the bundle was built with the flag) or
# null. In Python we evaluate the same gate at module-load time so the
# expensive submodule isn't pulled in unless the flag is set. Wrapped in
# try/except to tolerate not-yet-ported submodules.
try:
    extract_memories_module: Any = (
        importlib.import_module("hare.services.extract_memories.extract_memories")
        if feature("EXTRACT_MEMORIES")
        else None
    )
except ImportError:
    extract_memories_module = None
try:
    job_classifier_module: Any = (
        importlib.import_module("hare.jobs.classifier")
        if feature("TEMPLATES")
        else None
    )
except ImportError:
    job_classifier_module = None


# -- L51-58
from hare.services.auto_dream.auto_dream import execute_auto_dream
from hare.services.prompt_suggestion.prompt_suggestion import execute_prompt_suggestion
from hare.utils.env_utils import is_bare_mode, is_env_defined_falsy
from hare.utils.forked_agent import (
    create_cache_safe_params,
    save_cache_safe_params,
)


# -- L60-63
@dataclass
class StopHookResult:
    blocking_errors: list[Message]
    prevent_continuation: bool


# Yield-type union mirroring `StreamEvent | RequestStartEvent | Message |
# TombstoneMessage | ToolUseSummaryMessage` from the TS signature.
_HookYield = Union[
    StreamEvent,
    RequestStartEvent,
    Message,
    TombstoneMessage,
    ToolUseSummaryMessage,
]


# -- L65-81 / L456-473 — main async generator, line-by-line below.
async def handle_stop_hooks(
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    system_prompt: SystemPrompt,
    user_context: dict[str, str],
    system_context: dict[str, str],
    tool_use_context: ToolUseContext,
    query_source: QuerySource,
    stop_hook_active: Optional[bool] = None,
) -> AsyncGenerator[_HookYield, None]:
    # -- L82
    hook_start_time = time.time() * 1000

    # -- L84-91
    stop_hook_context: REPLHookContext = {
        "messages": [*messages_for_query, *assistant_messages],
        "systemPrompt": system_prompt,
        "userContext": user_context,
        "systemContext": system_context,
        "toolUseContext": tool_use_context,
        "querySource": query_source,
    }

    # -- L92-98
    # Only save params for main session queries — subagents must not
    # overwrite. Outside the prompt-suggestion gate: the REPL /btw command
    # and the side_question SDK control_request both read this snapshot,
    # and neither depends on prompt suggestions being enabled.
    if query_source == "repl_main_thread" or query_source == "sdk":
        save_cache_safe_params(create_cache_safe_params(stop_hook_context))

    # -- L100-132
    # Template job classification: when running as a dispatched job,
    # classify state after each turn. Gate on repl_main_thread so
    # background forks (extract-memories, auto-dream) don't pollute the
    # timeline with their own assistant messages. Await the classifier so
    # state.json is written before the turn returns — otherwise
    # `hare list` shows stale state for the gap. Env key hardcoded (vs
    # importing JOB_ENV_KEY from jobs/state) to match the require()-gated
    # jobs/ import pattern above; spawn.test.ts asserts the string matches.
    if (
        feature("TEMPLATES")
        and os.environ.get("CLAUDE_JOB_DIR")
        and query_source.startswith("repl_main_thread")
        and not tool_use_context.agent_id
    ):
        # Full turn history — assistantMessages resets each queryLoop
        # iteration, so tool calls from earlier iterations (Agent spawn,
        # then summary) need messagesForQuery to be visible in the
        # tool-call summary.
        turn_assistant_messages = [
            m
            for m in stop_hook_context["messages"]
            if getattr(m, "type", None) == "assistant"
        ]

        async def _classifier_call() -> None:
            try:
                await job_classifier_module.classify_and_write_state(  # type: ignore[union-attr]
                    os.environ["CLAUDE_JOB_DIR"], turn_assistant_messages
                )
            except Exception as err:  # noqa: BLE001
                log_for_debugging(
                    f"[job] classifier error: {error_message(err)}",
                    {"level": "error"},
                )

        try:
            await asyncio.wait_for(_classifier_call(), timeout=60.0)
        except asyncio.TimeoutError:
            # TS uses Promise.race with a 60s timer; we mirror by simply
            # letting the wait expire. Process exit semantics differ
            # (Node's setTimeout(...).unref() vs asyncio's task GC) but
            # the user-visible behaviour is identical.
            pass

    # -- L133-157 — !isBareMode bookkeeping
    # --bare / SIMPLE: skip background bookkeeping (prompt suggestion,
    # memory extraction, auto-dream). Scripted -p calls don't want
    # auto-memory or forked agents contending for resources during shutdown.
    if not is_bare_mode():
        # Inline env check for dead code elimination in external builds
        if not is_env_defined_falsy(
            os.environ.get("CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION")
        ):
            asyncio.ensure_future(execute_prompt_suggestion(stop_hook_context))
        if (
            feature("EXTRACT_MEMORIES")
            and not tool_use_context.agent_id
            and is_extract_mode_active()
        ):
            # Fire-and-forget in both interactive and non-interactive. For
            # -p/SDK, print.ts drains the in-flight promise after flushing
            # the response but before gracefulShutdownSync (see
            # drainPendingExtraction).
            asyncio.ensure_future(
                extract_memories_module.execute_extract_memories(  # type: ignore[union-attr]
                    stop_hook_context,
                    getattr(tool_use_context, "append_system_message", None),
                )
            )
        if not tool_use_context.agent_id:
            asyncio.ensure_future(
                execute_auto_dream(
                    stop_hook_context,
                    getattr(tool_use_context, "append_system_message", None),
                )
            )

    # -- L159-173 — CHICAGO_MCP auto-unhide / lock release at turn end
    # Main thread only — the CU lock is a process-wide module-level
    # variable, so a subagent's stopHooks releasing it leaves the main
    # thread's cleanup seeing isLockHeldLocally()===false → no exit
    # notification, and unhides mid-turn. Subagents don't start CU sessions
    # so this is a pure skip.
    if feature("CHICAGO_MCP") and not tool_use_context.agent_id:
        try:
            mod = importlib.import_module("hare.utils.computer_use.cleanup")
            await mod.cleanup_computer_use_after_turn(tool_use_context)
        except Exception:  # noqa: BLE001
            # Failures are silent — this is dogfooding cleanup, not
            # critical path
            pass

    # -- L175-472 — main try / catch
    try:
        # -- L176-178
        blocking_errors: list[Message] = []
        app_state = (
            tool_use_context.get_app_state()
            if tool_use_context.get_app_state is not None
            else None
        )
        permission_mode = app_state.tool_permission_context.mode if app_state else None

        # -- L180-189
        generator = execute_stop_hooks(
            permission_mode,
            tool_use_context.abort_controller.signal
            if tool_use_context.abort_controller is not None
            else None,
            None,
            stop_hook_active if stop_hook_active is not None else False,
            tool_use_context.agent_id,
            tool_use_context,
            [*messages_for_query, *assistant_messages],
            tool_use_context.agent_type,
        )

        # -- L191-198
        # Consume all progress messages and get blocking errors
        stop_hook_tool_use_id = ""
        hook_count = 0
        prevented_continuation = False
        stop_reason = ""
        has_output = False
        hook_errors: list[str] = []
        hook_infos: list[StopHookInfo] = []

        # -- L200-295
        async for result in generator:
            if result.get("message"):
                yield result["message"]
                # Track toolUseID from progress messages and count hooks
                if getattr(result["message"], "type", None) == "progress" and getattr(
                    result["message"], "tool_use_id", None
                ):
                    stop_hook_tool_use_id = result["message"].tool_use_id
                    hook_count += 1
                    # Extract hook command and prompt text from progress data
                    progress_data: HookProgress = result["message"].data  # type: ignore[assignment]
                    if getattr(progress_data, "command", None):
                        hook_infos.append(
                            StopHookInfo(
                                command=progress_data.command,
                                prompt_text=getattr(progress_data, "prompt_text", None),
                            )
                        )
                # Track errors and output from attachments
                if getattr(result["message"], "type", None) == "attachment":
                    attachment = result["message"].attachment
                    if "hookEvent" in attachment and (
                        attachment["hookEvent"] == "Stop"
                        or attachment["hookEvent"] == "SubagentStop"
                    ):
                        if attachment.get("type") == "hook_non_blocking_error":
                            hook_errors.append(
                                attachment.get("stderr")
                                or f"Exit code {attachment.get('exitCode')}"
                            )
                            # Non-blocking errors always have output
                            has_output = True
                        elif attachment.get("type") == "hook_error_during_execution":
                            hook_errors.append(attachment.get("content", ""))
                            has_output = True
                        elif attachment.get("type") == "hook_success":
                            # Check if successful hook produced any
                            # stdout/stderr
                            if (
                                attachment.get("stdout")
                                and attachment["stdout"].strip()
                            ) or (
                                attachment.get("stderr")
                                and attachment["stderr"].strip()
                            ):
                                has_output = True
                        # Extract per-hook duration for timing visibility.
                        # Hooks run in parallel; match by command + first
                        # unassigned entry.
                        if "durationMs" in attachment and "command" in attachment:
                            info = next(
                                (
                                    i
                                    for i in hook_infos
                                    if i.command == attachment["command"]
                                    and i.duration_ms is None
                                ),
                                None,
                            )
                            if info:
                                info.duration_ms = attachment["durationMs"]
            if result.get("blockingError"):
                user_message = create_user_message(
                    content=get_stop_hook_message(result["blockingError"]),
                    is_meta=True,  # Hide from UI (shown in summary instead)
                )
                blocking_errors.append(user_message)
                yield user_message
                has_output = True
                # Add to hookErrors so it appears in the summary
                hook_errors.append(result["blockingError"].blocking_error)
            # Check if hook wants to prevent continuation
            if result.get("preventContinuation"):
                prevented_continuation = True
                stop_reason = (
                    result.get("stopReason") or "Stop hook prevented continuation"
                )
                # Create attachment to track the stopped continuation (for
                # structured data)
                yield create_attachment_message(
                    {
                        "type": "hook_stopped_continuation",
                        "message": stop_reason,
                        "hookName": "Stop",
                        "toolUseID": stop_hook_tool_use_id,
                        "hookEvent": "Stop",
                    }
                )

            # Check if we were aborted during hook execution
            if (
                tool_use_context.abort_controller is not None
                and getattr(tool_use_context.abort_controller, "signal", None)
                is not None
                and getattr(tool_use_context.abort_controller.signal, "aborted", False)
            ):
                log_event(
                    "tengu_pre_stop_hooks_cancelled",
                    {
                        "queryChainId": (
                            tool_use_context.query_tracking.chain_id
                            if tool_use_context.query_tracking
                            else None
                        ),
                        "queryDepth": (
                            tool_use_context.query_tracking.depth
                            if tool_use_context.query_tracking
                            else None
                        ),
                    },
                )
                yield create_user_interruption_message(tool_use=False)
                return  # → StopHookResult(blocking_errors=[], prevent_continuation=True)

        # -- L297-323
        # Create summary system message if hooks ran
        if hook_count > 0:
            yield create_stop_hook_summary_message(
                hook_count,
                hook_infos,
                hook_errors,
                prevented_continuation,
                stop_reason,
                has_output,
                "suggestion",
                stop_hook_tool_use_id,
            )

            # Send notification about errors (shown in verbose/transcript
            # mode via ctrl+o)
            if len(hook_errors) > 0:
                expand_shortcut = get_shortcut_display(
                    "app:toggleTranscript",
                    "Global",
                    "ctrl+o",
                )
                if tool_use_context.add_notification is not None:
                    tool_use_context.add_notification(
                        {
                            "key": "stop-hook-error",
                            "text": f"Stop hook error occurred \u00b7 {expand_shortcut} to see",
                            "priority": "immediate",
                        }
                    )

        # -- L325-327
        if prevented_continuation:
            return  # StopHookResult(blocking_errors=[], prevent_continuation=True)

        # -- L329-332
        # Collect blocking errors from stop hooks
        if len(blocking_errors) > 0:
            return  # StopHookResult(blocking_errors=blocking_errors, prevent_continuation=False)

        # -- L334-453 — teammate-only hooks
        # After Stop hooks pass, run TeammateIdle and TaskCompleted hooks
        # if this is a teammate
        if is_teammate():
            teammate_name = get_agent_name() or ""
            team_name = get_team_name() or ""
            teammate_blocking_errors: list[Message] = []
            teammate_prevented_continuation = False
            teammate_stop_reason: Optional[str] = None
            # Each hook executor generates its own toolUseID — capture from
            # progress messages (same pattern as stopHookToolUseID at L142),
            # not the Stop ID.
            teammate_hook_tool_use_id = ""

            # Run TaskCompleted hooks for any in-progress tasks owned by
            # this teammate
            task_list_id = get_task_list_id()
            tasks = await list_tasks(task_list_id)
            in_progress_tasks = [
                t
                for t in tasks
                if getattr(t, "status", None) == "in_progress"
                and getattr(t, "owner", None) == teammate_name
            ]

            # -- L352-400
            for task in in_progress_tasks:
                task_completed_generator = execute_task_completed_hooks(
                    task.id,
                    task.subject,
                    task.description,
                    teammate_name,
                    team_name,
                    permission_mode,
                    tool_use_context.abort_controller.signal
                    if tool_use_context.abort_controller is not None
                    else None,
                    None,
                    tool_use_context,
                )

                async for result in task_completed_generator:
                    if result.get("message"):
                        if getattr(
                            result["message"], "type", None
                        ) == "progress" and getattr(
                            result["message"], "tool_use_id", None
                        ):
                            teammate_hook_tool_use_id = result["message"].tool_use_id
                        yield result["message"]
                    if result.get("blockingError"):
                        user_message = create_user_message(
                            content=get_task_completed_hook_message(
                                result["blockingError"]
                            ),
                            is_meta=True,
                        )
                        teammate_blocking_errors.append(user_message)
                        yield user_message
                    # Match Stop hook behavior: allow
                    # preventContinuation/stopReason
                    if result.get("preventContinuation"):
                        teammate_prevented_continuation = True
                        teammate_stop_reason = (
                            result.get("stopReason")
                            or "TaskCompleted hook prevented continuation"
                        )
                        yield create_attachment_message(
                            {
                                "type": "hook_stopped_continuation",
                                "message": teammate_stop_reason,
                                "hookName": "TaskCompleted",
                                "toolUseID": teammate_hook_tool_use_id,
                                "hookEvent": "TaskCompleted",
                            }
                        )
                    if (
                        tool_use_context.abort_controller is not None
                        and getattr(tool_use_context.abort_controller, "signal", None)
                        is not None
                        and getattr(
                            tool_use_context.abort_controller.signal,
                            "aborted",
                            False,
                        )
                    ):
                        return

            # -- L402-441
            # Run TeammateIdle hooks
            teammate_idle_generator = execute_teammate_idle_hooks(
                teammate_name,
                team_name,
                permission_mode,
                tool_use_context.abort_controller.signal
                if tool_use_context.abort_controller is not None
                else None,
            )

            async for result in teammate_idle_generator:
                if result.get("message"):
                    if getattr(
                        result["message"], "type", None
                    ) == "progress" and getattr(result["message"], "tool_use_id", None):
                        teammate_hook_tool_use_id = result["message"].tool_use_id
                    yield result["message"]
                if result.get("blockingError"):
                    user_message = create_user_message(
                        content=get_teammate_idle_hook_message(result["blockingError"]),
                        is_meta=True,
                    )
                    teammate_blocking_errors.append(user_message)
                    yield user_message
                # Match Stop hook behavior: allow
                # preventContinuation/stopReason
                if result.get("preventContinuation"):
                    teammate_prevented_continuation = True
                    teammate_stop_reason = (
                        result.get("stopReason")
                        or "TeammateIdle hook prevented continuation"
                    )
                    yield create_attachment_message(
                        {
                            "type": "hook_stopped_continuation",
                            "message": teammate_stop_reason,
                            "hookName": "TeammateIdle",
                            "toolUseID": teammate_hook_tool_use_id,
                            "hookEvent": "TeammateIdle",
                        }
                    )
                if (
                    tool_use_context.abort_controller is not None
                    and getattr(tool_use_context.abort_controller, "signal", None)
                    is not None
                    and getattr(
                        tool_use_context.abort_controller.signal, "aborted", False
                    )
                ):
                    return

            # -- L443-452
            if teammate_prevented_continuation:
                return

            if len(teammate_blocking_errors) > 0:
                return  # StopHookResult(blocking_errors=teammate_blocking_errors, prevent_continuation=False)

        # -- L455
        return  # StopHookResult(blocking_errors=[], prevent_continuation=False)

    # -- L456-472
    except Exception as error:  # noqa: BLE001
        duration_ms = int(time.time() * 1000 - hook_start_time)
        log_event(
            "tengu_stop_hook_error",
            {
                "duration": duration_ms,
                "queryChainId": (
                    tool_use_context.query_tracking.chain_id
                    if tool_use_context.query_tracking
                    else None
                ),
                "queryDepth": (
                    tool_use_context.query_tracking.depth
                    if tool_use_context.query_tracking
                    else None
                ),
            },
        )
        # Yield a system message that is not visible to the model for the
        # user to debug their hook.
        yield create_system_message(
            f"Stop hook failed: {error_message(error)}",
            "warning",
        )
        return  # StopHookResult(blocking_errors=[], prevent_continuation=False)
