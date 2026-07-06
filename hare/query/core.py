"""Core query loop.

Port of: src/query.ts

This module owns the model -> tools -> model conversation loop.  The TS source
has many feature-gated recovery paths; this Python port keeps the same primary
state machine shape and delegates to the already-ported query submodules where
they exist.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass, field, replace
from typing import Any, AsyncGenerator, Callable, Iterable, Optional, Sequence
from uuid import uuid4

from hare.query.config import build_query_config
from hare.query.deps import QueryDeps, production_deps
from hare.query.stop_hooks import handle_stop_hooks
from hare.query.token_budget import check_token_budget, create_budget_tracker
from hare.utils.hooks import execute_stop_failure_hooks
from hare.query.transitions import Continue, Terminal, normalize_query_loop_transition
from hare.services.analytics import log_event
from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale
from hare.services.tools import StreamingToolExecutor
from hare.services.tools.tool_orchestration import run_tools
from hare.services.tool_use_summary import generate_tool_use_summary
from hare.tool import (
    CanUseToolFn,
    Tool,
    ToolUseContext,
    QueryChainTracking,
    find_tool_by_name,
)
from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    AttachmentMessage,
    Message,
    QueryYield,
    RequestStartEvent,
    StreamEvent,
    TombstoneMessage,
    ToolUseSummaryMessage,
    UserMessage,
)
from hare.utils.bundle_feature import feature
from hare.utils.log import log_error
from hare.utils.image_validation import ImageSizeError
from hare.utils.tokens import (
    final_context_tokens_from_last_response,
    token_count_with_estimation,
)
from hare.utils.query_profiler import query_checkpoint
from hare.utils.headless_profiler import headless_profiler_checkpoint
from hare.utils.messages import (
    create_assistant_api_error_message,
    create_attachment_message,
    create_microcompact_boundary_message,
    create_system_message,
    create_tool_use_summary_message,
    create_user_interruption_message,
    create_user_message,
    ensure_tool_result_pairing,
    get_messages_after_compact_boundary,
    normalize_messages_for_api,
    strip_signature_blocks,
)
from hare.utils.command_lifecycle import notify_command_lifecycle
from hare.utils.message_queue_manager import (
    get_command_queue,
    is_slash_command,
)
from hare.utils.model import get_runtime_main_loop_model

MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
ESCALATED_MAX_TOKENS = 64_000
PROMPT_TOO_LONG_ERROR_MESSAGE = (
    "Your conversation has grown too long for this model. Run /compact and try again."
)


@dataclass
class QueryParams:
    messages: list[Message] = field(default_factory=list)
    system_prompt: list[str] = field(default_factory=list)
    user_context: dict[str, str] = field(default_factory=dict)
    system_context: dict[str, str] = field(default_factory=dict)
    can_use_tool: Optional[CanUseToolFn] = None
    tool_use_context: Optional[ToolUseContext] = None
    fallback_model: Optional[str] = None
    query_source: str = "sdk"
    max_output_tokens_override: Optional[int] = None
    max_turns: Optional[int] = None
    skip_cache_write: bool = False
    task_budget: Optional[dict[str, float]] = None
    deps: Optional[QueryDeps] = None
    on_terminal: Optional[Callable[[Terminal], None]] = None
    on_transition: Optional[Callable[[Continue], None]] = None
    on_terminal_transition: Optional[Callable[[Continue | None], None]] = None


@dataclass
class _State:
    messages: list[Message] = field(default_factory=list)
    tool_use_context: ToolUseContext = field(default_factory=ToolUseContext)
    auto_compact_tracking: Optional[dict[str, Any]] = None
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    max_output_tokens_override: Optional[int] = None
    pending_tool_use_summary: Optional[Any] = None
    stop_hook_active: Optional[bool] = None
    turn_count: int = 1
    transition: Optional[Continue] = None


@dataclass
class _ToolUpdate:
    message: Optional[Message] = None
    new_context: Optional[ToolUseContext] = None


@dataclass
class _PendingPrefetch:
    task: asyncio.Task[Any]
    consumed_on_iteration: int = -1


@dataclass
class _PendingCacheEdits:
    trigger: str
    deleted_tool_ids: list[str]
    baseline_cache_deleted_tokens: int = 0


async def query(params: QueryParams) -> AsyncGenerator[QueryYield, None]:
    """Public query entrypoint.

    TS returns a terminal value from the async generator. Python async
    generators cannot expose a return value through ``async for``, so the
    terminal reason is retained internally and observable through yielded
    messages such as max-turn attachments.
    """
    consumed_command_uuids: list[str] = []
    async for item in _query_loop(params, consumed_command_uuids):
        yield item
    for uuid in consumed_command_uuids:
        notify_command_lifecycle(uuid, "completed")


async def _query_loop(
    params: QueryParams,
    consumed_command_uuids: list[str],
) -> AsyncGenerator[QueryYield, None]:
    terminal = Terminal(reason="completed")

    def finish(
        reason: str, *, error: Any = None, turn_count: Optional[int] = None
    ) -> None:
        terminal.reason = reason
        terminal.error = error
        terminal.turn_count = turn_count
        if params.on_terminal_transition is not None:
            try:
                params.on_terminal_transition(state.transition)
            except Exception:
                pass
        if params.on_terminal is not None:
            try:
                params.on_terminal(terminal)
            except Exception:
                pass

    def mark_transition(transition: Continue) -> Continue:
        transition = normalize_query_loop_transition(transition)
        if params.on_transition is not None:
            try:
                params.on_transition(transition)
            except Exception:
                pass
        return transition

    deps = params.deps or production_deps()
    config = build_query_config()
    budget_tracker = create_budget_tracker() if feature("TOKEN_BUDGET") else None
    task_budget_remaining: Optional[int] = None
    pending_memory_prefetch = _start_memory_prefetch_once(
        list(params.messages),
        params.tool_use_context or ToolUseContext(),
    )

    state = _State(
        messages=list(params.messages),
        tool_use_context=params.tool_use_context or ToolUseContext(),
        max_output_tokens_override=params.max_output_tokens_override,
    )

    while True:
        tool_use_context = state.tool_use_context
        messages = state.messages
        turn_count = state.turn_count
        tracking = state.auto_compact_tracking
        pending_tool_use_summary = state.pending_tool_use_summary

        yield RequestStartEvent(type="stream_request_start")
        query_checkpoint("query_fn_entry")
        # Record query start for headless latency tracking (skip for subagents)
        if not tool_use_context.agent_id:
            headless_profiler_checkpoint("query_started")
        _snapshot_output_tokens_for_turn(params.task_budget)

        query_tracking = _next_query_tracking(tool_use_context, deps)
        tool_use_context = replace(tool_use_context, query_tracking=query_tracking)
        pending_skill_prefetch = _start_skill_prefetch_for_turn(
            messages,
            tool_use_context,
        )

        messages_for_query = list(get_messages_after_compact_boundary(messages))

        # Apply tool result budget before snip/microcompact (TS L365-394)
        messages_for_query = _maybe_apply_tool_result_budget(
            messages_for_query,
            tool_use_context,
            params.query_source,
        )

        # Apply HISTORY_SNIP before microcompact (TS L401-410)
        snip_tokens_freed = 0
        query_checkpoint("query_snip_start")
        messages_for_query, snip_tokens_freed, snip_boundary = _maybe_snip_compact(
            messages_for_query,
        )
        if snip_boundary is not None:
            yield snip_boundary
        query_checkpoint("query_snip_end")

        query_checkpoint("query_microcompact_start")
        microcompact_result = await _maybe_microcompact_result(
            deps, messages_for_query, tool_use_context, params.query_source
        )
        messages_for_query = _extract_microcompact_messages(
            microcompact_result, messages_for_query
        )
        pending_cache_edits = _extract_pending_cache_edits(microcompact_result)
        query_checkpoint("query_microcompact_end")

        # Apply context collapse projection before autocompact (TS L428-447)
        messages_for_query = await _maybe_apply_context_collapse(
            messages_for_query,
            tool_use_context,
            params.query_source,
        )

        query_checkpoint("query_autocompact_start")
        compaction = await _maybe_autocompact(
            deps,
            messages_for_query,
            tool_use_context,
            params,
            tracking,
            snip_tokens_freed,
        )
        if compaction.messages is not None:
            if params.task_budget:
                before_budget = (
                    task_budget_remaining
                    if task_budget_remaining is not None
                    else int(params.task_budget.get("total", 0))
                )
                task_budget_remaining = _apply_task_budget_spend(
                    task_budget_remaining,
                    int(params.task_budget.get("total", 0)),
                    messages_for_query,
                )
                log_event(
                    "tengu_task_budget_decremented",
                    {
                        "path": "autocompact",
                        "before": before_budget,
                        "after": task_budget_remaining,
                        "spent": max(0, before_budget - (task_budget_remaining or 0)),
                        "query_source": params.query_source,
                    },
                )
            messages_for_query = compaction.messages
            tracking = compaction.tracking
            for msg in compaction.yielded_messages:
                yield msg
        elif compaction.tracking is not None:
            tracking = compaction.tracking
        query_checkpoint("query_autocompact_end")

        if (
            compaction.messages is None
            and _should_run_blocking_limit_precheck(params.query_source)
            and _is_at_blocking_limit(messages_for_query, tool_use_context)
        ):
            yield create_assistant_api_error_message(
                content=PROMPT_TOO_LONG_ERROR_MESSAGE,
                error="invalid_request",
            )
            finish("blocking_limit")
            return

        tool_use_context = replace(tool_use_context, messages=messages_for_query)

        query_checkpoint("query_setup_start")
        assistant_messages: list[AssistantMessage] = []
        tool_results: list[UserMessage | AttachmentMessage] = []
        tool_use_blocks: list[dict[str, Any]] = []
        seen_tool_use_ids: set[str] = set()
        needs_follow_up = False

        current_model = _current_model(tool_use_context, messages_for_query)
        attempted_fallback = False
        streaming_tool_executor = _maybe_create_streaming_tool_executor(
            config.gates.streaming_tool_execution,
            tool_use_context,
            params.can_use_tool or _allow_tool,
        )

        # Create fetch wrapper for Ant debugging (TS L582-590)
        dump_prompts_fetch = (
            _create_dump_prompts_fetch(tool_use_context, config)
            if config.gates.is_ant
            else None
        )

        query_checkpoint("query_setup_end")

        while True:
            try:
                query_checkpoint("query_api_streaming_start")
                streaming_fallback_occurred = False

                def _mark_streaming_fallback() -> None:
                    nonlocal streaming_fallback_occurred
                    streaming_fallback_occurred = True

                async for message in _stream_model_turn(
                    deps=deps,
                    messages=messages_for_query,
                    system_prompt=_full_system_prompt(
                        params.system_prompt, params.system_context
                    ),
                    user_context=params.user_context,
                    tool_use_context=tool_use_context,
                    model=current_model,
                    fallback_model=params.fallback_model,
                    query_source=params.query_source,
                    max_output_tokens_override=state.max_output_tokens_override,
                    skip_cache_write=params.skip_cache_write,
                    task_budget_payload=_build_task_budget_payload(
                        params.task_budget,
                        task_budget_remaining,
                    ),
                    on_streaming_fallback=_mark_streaming_fallback,
                ):
                    if streaming_fallback_occurred:
                        streaming_fallback_occurred = False
                        orphaned_count = len(assistant_messages)
                        for orphan in assistant_messages:
                            yield TombstoneMessage(type="tombstone", message=orphan)
                        if orphaned_count > 0:
                            log_event(
                                "tengu_orphaned_messages_tombstoned",
                                {"orphanedMessageCount": orphaned_count},
                            )
                        assistant_messages.clear()
                        tool_results.clear()
                        tool_use_blocks.clear()
                        needs_follow_up = False
                        streaming_tool_executor = _reset_streaming_executor(
                            streaming_tool_executor,
                            config.gates.streaming_tool_execution,
                            tool_use_context,
                            params.can_use_tool or _allow_tool,
                        )

                    if isinstance(message, AssistantMessage):
                        if (
                            assistant_messages
                            and assistant_messages[-1].uuid == message.uuid
                        ):
                            assistant_messages[-1] = message
                        else:
                            assistant_messages.append(message)
                        blocks = _extract_tool_use_blocks(message)
                        if blocks:
                            new_blocks = []
                            for block in blocks:
                                tool_use_id = str(block.get("id", ""))
                                if tool_use_id and tool_use_id in seen_tool_use_ids:
                                    continue
                                if tool_use_id:
                                    seen_tool_use_ids.add(tool_use_id)
                                new_blocks.append(block)
                            if new_blocks:
                                tool_use_blocks.extend(new_blocks)
                                needs_follow_up = True
                                _streaming_executor_add_tools(
                                    streaming_tool_executor, new_blocks, message
                                )
                        if _should_withhold_assistant_error(message):
                            continue
                    yield _backfill_tool_use_inputs_for_yield(
                        message,
                        tool_use_context.options.tools,
                    )
                    if streaming_tool_executor is not None and not _is_aborted(
                        tool_use_context
                    ):
                        for result in _streaming_executor_get_completed_results(
                            streaming_tool_executor
                        ):
                            if result.message is not None:
                                yield result.message
                                tool_results.extend(
                                    m
                                    for m in normalize_messages_for_api(
                                        [result.message], tool_use_context.options.tools
                                    )
                                    if getattr(m, "type", None) == "user"
                                )
                            if result.new_context is not None:
                                tool_use_context = result.new_context
                query_checkpoint("query_api_streaming_end")
                break
            except ImageSizeError as error:
                yield create_assistant_api_error_message(content=str(error))
                finish("image_error")
                return
            except Exception as error:  # noqa: BLE001
                if (
                    not attempted_fallback
                    and params.fallback_model
                    and _is_fallback_triggered_error(error)
                ):
                    attempted_fallback = True
                    orphaned_count = len(assistant_messages)
                    for orphan in assistant_messages:
                        yield TombstoneMessage(type="tombstone", message=orphan)
                    if orphaned_count > 0:
                        log_event(
                            "tengu_orphaned_messages_tombstoned",
                            {"orphanedMessageCount": orphaned_count},
                        )
                    async for msg in _yield_missing_tool_result_blocks(
                        assistant_messages, "Model fallback triggered"
                    ):
                        yield msg
                    assistant_messages.clear()
                    tool_results.clear()
                    tool_use_blocks.clear()
                    needs_follow_up = False
                    _cancel_pending_tool_use_summary(state.pending_tool_use_summary)
                    streaming_tool_executor = _reset_streaming_executor(
                        streaming_tool_executor,
                        config.gates.streaming_tool_execution,
                        tool_use_context,
                        params.can_use_tool or _allow_tool,
                    )
                    current_model = params.fallback_model
                    tool_use_context.options.main_loop_model = current_model
                    messages_for_query = strip_signature_blocks(messages_for_query)
                    log_event(
                        "tengu_model_fallback_triggered",
                        {"fallback_model": current_model},
                    )
                    state = replace(
                        state,
                        pending_tool_use_summary=None,
                        stop_hook_active=None,
                        max_output_tokens_override=None,
                        transition=None,
                    )
                    yield create_system_message(
                        f"Switched to fallback model: {current_model}",
                        "warning",
                    )
                    continue

                log_error(error)
                async for msg in _yield_missing_tool_result_blocks(
                    assistant_messages, str(error)
                ):
                    yield msg
                streaming_tool_executor = _reset_streaming_executor(
                    streaming_tool_executor,
                    config.gates.streaming_tool_execution,
                    tool_use_context,
                    params.can_use_tool or _allow_tool,
                )
                _cancel_pending_tool_use_summary(state.pending_tool_use_summary)
                yield create_assistant_api_error_message(content=str(error))
                finish("model_error", error=error)
                return

        # Fire post-sampling hooks after model response completes (TS L999-1009)
        if assistant_messages:
            _execute_post_sampling_hooks(
                messages_for_query,
                assistant_messages,
                params,
                tool_use_context,
            )

        if _is_aborted(tool_use_context):
            if streaming_tool_executor is not None:
                async for update in _streaming_executor_get_remaining_results(
                    streaming_tool_executor
                ):
                    if update.message is not None:
                        yield update.message
            else:
                async for msg in _yield_missing_tool_result_blocks(
                    assistant_messages, "Interrupted by user"
                ):
                    yield msg
            # CHICAGO_MCP cleanup on interrupt (TS L1033-1042)
            _maybe_chicago_mcp_cleanup(tool_use_context)
            if _abort_reason(tool_use_context) != "interrupt":
                yield create_user_interruption_message(tool_use=False)
            finish("aborted_streaming")
            return

        # Yield tool use summary from previous turn (TS L1054-1060)
        # BEFORE !needsFollowUp — resolves during model streaming
        if pending_tool_use_summary is not None:
            summary_message = await _resolve_pending_tool_use_summary(
                pending_tool_use_summary
            )
            if summary_message is not None:
                yield summary_message

        if not needs_follow_up:
            last_assistant = assistant_messages[-1] if assistant_messages else None
            surfaced_terminal_error = False

            if _is_withheld_max_output_tokens(last_assistant):
                cap_enabled = bool(
                    get_feature_value_cached_may_be_stale("tengu_otk_slot_v1", False)
                )
                if (
                    cap_enabled
                    and state.max_output_tokens_override is None
                    and "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in os.environ
                ):
                    state = replace(
                        state,
                        messages=messages_for_query,
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=tracking,
                        max_output_tokens_override=ESCALATED_MAX_TOKENS,
                        pending_tool_use_summary=None,
                        stop_hook_active=None,
                        transition=mark_transition(
                            Continue(reason="max_output_tokens_escalate")
                        ),
                    )
                    log_event(
                        "tengu_max_tokens_escalate",
                        {"escalatedTo": ESCALATED_MAX_TOKENS},
                    )
                    continue

                if (
                    state.max_output_tokens_recovery_count
                    < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT
                ):
                    attempt = state.max_output_tokens_recovery_count + 1
                    recovery_message = create_user_message(
                        content=(
                            "Output token limit hit. Resume directly - no apology, "
                            "no recap. Pick up mid-thought if needed and break "
                            "remaining work into smaller pieces."
                        ),
                        is_meta=True,
                    )
                    state = replace(
                        state,
                        messages=[
                            *messages_for_query,
                            *assistant_messages,
                            recovery_message,
                        ],
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=tracking,
                        max_output_tokens_recovery_count=attempt,
                        max_output_tokens_override=None,
                        pending_tool_use_summary=None,
                        stop_hook_active=None,
                        transition=mark_transition(
                            Continue(
                                reason="max_output_tokens_recovery",
                                attempt=attempt,
                            )
                        ),
                    )
                    continue

                yield last_assistant
                # TS query.ts:1262 — fire-and-forget stop failure hooks
                # after max_output_tokens recovery is exhausted
                asyncio.create_task(
                    execute_stop_failure_hooks(last_assistant, tool_use_context)
                )
                surfaced_terminal_error = True

            if _is_withheld_prompt_too_long(last_assistant):
                log_event("tengu_withheld_prompt_too_long", {"phase": "post_stream"})
                collapsed = await _try_collapse_drain_recovery(
                    state=state,
                    params=params,
                    tool_use_context=tool_use_context,
                    messages_for_query=messages_for_query,
                    mark_transition=mark_transition,
                )
                if collapsed is not None:
                    log_event(
                        "tengu_collapse_drain_recovery_succeeded",
                        {
                            "query_source": params.query_source,
                            "kind": "prompt_too_long",
                            "outcome": "succeeded",
                        },
                    )
                    state = collapsed
                    continue
                recovered = await _try_reactive_recovery(
                    state=state,
                    params=params,
                    tool_use_context=tool_use_context,
                    messages_for_query=messages_for_query,
                    tracking=tracking,
                    task_budget_remaining=task_budget_remaining,
                    mark_transition=mark_transition,
                )
                if recovered is not None:
                    log_event(
                        "tengu_reactive_recovery_succeeded",
                        {
                            "kind": "prompt_too_long",
                            "query_source": params.query_source,
                            "outcome": "succeeded",
                        },
                    )
                    state, task_budget_remaining, recovery_messages = recovered
                    for m in recovery_messages:
                        yield m
                    continue
                log_event(
                    "tengu_reactive_recovery_not_applied",
                    {
                        "kind": "prompt_too_long",
                        "query_source": params.query_source,
                        "outcome": "not_applied",
                    },
                )
                yield last_assistant
                surfaced_terminal_error = True
                # TS query.ts:1173 — fire-and-forget stop failure hooks
                asyncio.create_task(
                    execute_stop_failure_hooks(last_assistant, tool_use_context)
                )
                finish("prompt_too_long")
                return
            if _is_withheld_media_error(last_assistant):
                log_event("tengu_withheld_media_error", {"phase": "post_stream"})
                recovered = await _try_reactive_recovery(
                    state=state,
                    params=params,
                    tool_use_context=tool_use_context,
                    messages_for_query=messages_for_query,
                    tracking=tracking,
                    task_budget_remaining=task_budget_remaining,
                    mark_transition=mark_transition,
                )
                if recovered is not None:
                    log_event(
                        "tengu_reactive_recovery_succeeded",
                        {
                            "kind": "media",
                            "query_source": params.query_source,
                            "outcome": "succeeded",
                        },
                    )
                    state, task_budget_remaining, recovery_messages = recovered
                    for m in recovery_messages:
                        yield m
                    continue
                log_event(
                    "tengu_reactive_recovery_not_applied",
                    {
                        "kind": "media",
                        "query_source": params.query_source,
                        "outcome": "not_applied",
                    },
                )
                yield last_assistant
                surfaced_terminal_error = True
                finish("image_error")
                return

            if surfaced_terminal_error:
                finish("completed")
                return

            if last_assistant and last_assistant.is_api_error_message:
                finish("completed")
                return

            stop_hook = await _run_stop_hooks_collect(
                messages_for_query=messages_for_query,
                assistant_messages=assistant_messages,
                params=params,
                tool_use_context=tool_use_context,
                stop_hook_active=state.stop_hook_active,
            )
            for hook_msg in stop_hook["messages"]:
                yield hook_msg
            if stop_hook["prevent_continuation"]:
                finish("stop_hook_prevented")
                return
            if stop_hook["blocking_errors"]:
                state = replace(
                    state,
                    messages=[
                        *messages_for_query,
                        *assistant_messages,
                        *stop_hook["blocking_errors"],
                    ],
                    tool_use_context=tool_use_context,
                    auto_compact_tracking=tracking,
                    max_output_tokens_recovery_count=0,
                    has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                    max_output_tokens_override=None,
                    pending_tool_use_summary=None,
                    stop_hook_active=True,
                    transition=mark_transition(Continue(reason="stop_hook_blocking")),
                )
                continue

            if feature("TOKEN_BUDGET") and budget_tracker is not None:
                decision = check_token_budget(
                    budget_tracker,
                    tool_use_context.agent_id,
                    _get_current_turn_token_budget(),
                    _get_turn_output_tokens(),
                )
                if decision.action == "continue":
                    _increment_budget_continuation_count()
                    state = replace(
                        state,
                        messages=[
                            *messages_for_query,
                            *assistant_messages,
                            create_user_message(
                                content=decision.nudge_message,
                                is_meta=True,
                            ),
                        ],
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=tracking,
                        max_output_tokens_recovery_count=0,
                        has_attempted_reactive_compact=False,
                        max_output_tokens_override=None,
                        pending_tool_use_summary=None,
                        stop_hook_active=None,
                        transition=mark_transition(
                            Continue(reason="token_budget_continuation")
                        ),
                    )
                    continue

                # TS query.ts:1342-1354 — log completion event when budget tracking ends
                if decision.action == "stop":
                    ce = decision.completion_event  # type: ignore[union-attr]
                    if ce is not None:
                        if ce.diminishing_returns:
                            log_event(
                                "tengu_token_budget_diminishing_returns",
                                {
                                    "query_source": params.query_source,
                                    "pct": ce.pct,
                                },
                            )
                        log_event(
                            "tengu_token_budget_completed",
                            {
                                "continuation_count": ce.continuation_count,
                                "pct": ce.pct,
                                "turn_tokens": ce.turn_tokens,
                                "budget": ce.budget,
                                "diminishing_returns": ce.diminishing_returns,
                                "duration_ms": ce.duration_ms,
                                "queryChainId": getattr(
                                    getattr(tool_use_context, "query_tracking", None),
                                    "chain_id",
                                    "",
                                ),
                                "queryDepth": getattr(
                                    getattr(tool_use_context, "query_tracking", None),
                                    "depth",
                                    0,
                                ),
                            },
                        )

            finish("completed")
            return

        updated_tool_use_context = tool_use_context
        should_prevent_continuation = False
        query_checkpoint("query_tool_execution_start")
        tool_update_stream = (
            _run_tool_updates_with_streaming_executor(streaming_tool_executor)
            if streaming_tool_executor is not None
            else _run_tool_updates(
                tool_use_blocks,
                assistant_messages,
                params.can_use_tool or _allow_tool,
                tool_use_context,
            )
        )
        async for update in tool_update_stream:
            if update.message is not None:
                yield update.message
                if (
                    getattr(update.message, "type", None) == "attachment"
                    and getattr(update.message, "attachment", {}).get("type")
                    == "hook_stopped_continuation"
                ):
                    should_prevent_continuation = True
                tool_results.extend(
                    m
                    for m in normalize_messages_for_api(
                        [update.message], tool_use_context.options.tools
                    )
                    if getattr(m, "type", None) == "user"
                )
            if update.new_context is not None:
                updated_tool_use_context = update.new_context
        query_checkpoint("query_tool_execution_end")

        # Track turns after compaction (TS L1523-1533)
        if tracking and tracking.get("compacted"):
            tracking["turnCounter"] = tracking.get("turnCounter", 0) + 1
            log_event(
                "tengu_post_autocompact_turn",
                {
                    "turnId": tracking.get("turnId", ""),
                    "turnCounter": tracking["turnCounter"],
                    "queryChainId": query_tracking.chain_id,
                    "queryDepth": query_tracking.depth,
                },
            )

        if config.gates.streaming_tool_execution:
            log_event(
                "tengu_streaming_tool_execution_used"
                if streaming_tool_executor is not None
                else "tengu_streaming_tool_execution_not_used",
                {"tool_count": len(tool_use_blocks)},
            )

        if feature("CACHED_MICROCOMPACT") and pending_cache_edits is not None:
            last_assistant = assistant_messages[-1] if assistant_messages else None
            usage = getattr(getattr(last_assistant, "message", None), "usage", None)
            cumulative_deleted = (
                usage.get("cache_deleted_input_tokens", 0)
                if isinstance(usage, dict)
                else 0
            )
            deleted_tokens = max(
                0,
                int(cumulative_deleted)
                - int(pending_cache_edits.baseline_cache_deleted_tokens),
            )
            if deleted_tokens > 0:
                yield create_microcompact_boundary_message(
                    pending_cache_edits.trigger,
                    0,
                    deleted_tokens,
                    pending_cache_edits.deleted_tool_ids,
                    [],
                )

        if _is_aborted(tool_use_context):
            async for msg in _yield_missing_tool_result_blocks_for_unresolved(
                assistant_messages,
                tool_results,
                "Interrupted by user",
            ):
                yield msg
            # CHICAGO_MCP cleanup on abort mid-tool-call (TS L1489-1498)
            _maybe_chicago_mcp_cleanup(tool_use_context)
            if _abort_reason(tool_use_context) != "interrupt":
                yield create_user_interruption_message(tool_use=True)
            next_turn_count = turn_count + 1
            if params.max_turns and next_turn_count > params.max_turns:
                yield create_attachment_message(
                    {
                        "type": "max_turns_reached",
                        "maxTurns": params.max_turns,
                        "turnCount": next_turn_count,
                    }
                )
            finish("aborted_tools")
            return

        if should_prevent_continuation:
            finish("hook_stopped")
            return

        log_event(
            "tengu_query_before_attachments",
            {
                "messagesForQueryCount": len(messages_for_query),
                "assistantMessagesCount": len(assistant_messages),
                "toolResultsCount": len(tool_results),
                "queryChainId": query_tracking.chain_id,
                "queryDepth": query_tracking.depth,
            },
        )

        # SleepRan-based priority: when Sleep tool was used, drain 'later' too (TS L1566)
        sleep_ran = any(
            block.get("name") == SLEEP_TOOL_NAME for block in tool_use_blocks
        )
        allowed_priorities = ("now", "next", "later") if sleep_ran else ("now", "next")

        # Snapshot queued commands before processing (TS L1570-1578)
        # Consume queue in two phases: snapshot → process → remove
        # This way if attachment processing throws, commands stay in queue for retry
        queued_commands_snapshot = _snapshot_queued_commands(
            params.query_source,
            tool_use_context.agent_id,
            allowed_priorities,
        )
        # Phase 1: process queued commands as attachments
        for cmd in queued_commands_snapshot:
            attachment = _command_to_attachment(cmd)
            if attachment is not None:
                yield attachment
                tool_results.append(attachment)
        # Phase 2: remove consumed commands from queue
        consumed_commands = [
            cmd
            for cmd in queued_commands_snapshot
            if getattr(cmd, "mode", "prompt") in ("prompt", "task-notification")
        ]
        for cmd in consumed_commands:
            if getattr(cmd, "uuid", None):
                consumed_command_uuids.append(cmd.uuid)
                notify_command_lifecycle(cmd.uuid, "started")
        _remove_consumed_commands(consumed_commands)

        memory_attachments = await _consume_memory_prefetch_if_ready(
            pending_memory_prefetch,
            tool_use_context,
            turn_count,
        )
        for attachment in memory_attachments:
            yield attachment
            tool_results.append(attachment)

        skill_attachments = await _collect_skill_prefetch(
            pending_skill_prefetch,
        )
        for attachment in skill_attachments:
            yield attachment
            tool_results.append(attachment)

        # Instrumentation: Track file change attachments after they're added (TS L1646-1657)
        file_change_attachment_count = sum(
            1
            for tr in tool_results
            if getattr(tr, "type", None) == "attachment"
            and getattr(tr, "attachment", {}).get("type") == "edited_text_file"
        )
        log_event(
            "tengu_query_after_attachments",
            {
                "totalToolResultsCount": len(tool_results),
                "fileChangeAttachmentCount": file_change_attachment_count,
                "queryChainId": query_tracking.chain_id,
                "queryDepth": query_tracking.depth,
            },
        )

        next_pending_tool_use_summary = _schedule_tool_use_summary(
            enabled=config.gates.emit_tool_use_summaries,
            tool_use_blocks=tool_use_blocks,
            tool_results=tool_results,
            tool_use_context=tool_use_context,
            assistant_messages=assistant_messages,
        )

        if updated_tool_use_context.options.refresh_tools is not None:
            refreshed = updated_tool_use_context.options.refresh_tools()
            if refreshed is not updated_tool_use_context.options.tools:
                options = replace(updated_tool_use_context.options, tools=refreshed)
                updated_tool_use_context = replace(
                    updated_tool_use_context, options=options
                )

        next_turn_count = turn_count + 1
        if params.max_turns and next_turn_count > params.max_turns:
            yield create_attachment_message(
                {
                    "type": "max_turns_reached",
                    "maxTurns": params.max_turns,
                    "turnCount": next_turn_count,
                }
            )
            finish("max_turns", turn_count=next_turn_count)
            return

        # Periodic task summary for 'hare ps' — fire-and-forget (TS L1681-1702)
        _maybe_generate_task_summary(
            messages_for_query,
            assistant_messages,
            tool_results,
            params,
            tool_use_context,
        )

        query_checkpoint("query_recursive_call")
        state = replace(
            state,
            messages=[*messages_for_query, *assistant_messages, *tool_results],
            tool_use_context=replace(
                updated_tool_use_context, query_tracking=query_tracking
            ),
            auto_compact_tracking=tracking,
            turn_count=next_turn_count,
            max_output_tokens_recovery_count=0,
            has_attempted_reactive_compact=False,
            max_output_tokens_override=None,
            pending_tool_use_summary=next_pending_tool_use_summary,
            stop_hook_active=None,
            transition=mark_transition(Continue(reason="next_turn")),
        )


async def _stream_model_turn(
    *,
    deps: QueryDeps,
    messages: list[Message],
    system_prompt: list[str],
    user_context: dict[str, str],
    tool_use_context: ToolUseContext,
    model: str,
    fallback_model: Optional[str],
    query_source: str,
    max_output_tokens_override: Optional[int],
    skip_cache_write: bool,
    task_budget_payload: Optional[dict[str, float]],
    on_streaming_fallback: Optional[Callable[[], None]] = None,
) -> AsyncGenerator[QueryYield, None]:
    app_state = None
    permission_context = None
    if tool_use_context.get_app_state is not None:
        try:
            app_state = tool_use_context.get_app_state()
            permission_context = getattr(app_state, "tool_permission_context", None)
        except Exception:
            app_state = None
            permission_context = None

    agent_definitions = tool_use_context.options.agent_definitions or {}
    mcp_clients = list(getattr(tool_use_context.options, "mcp_clients", []) or [])
    messages = ensure_tool_result_pairing(messages)
    messages = normalize_messages_for_api(messages, tool_use_context.options.tools)
    payload = {
        "messages": _prepend_user_context(messages, user_context),
        "system_prompt": system_prompt,
        "thinking_config": tool_use_context.options.thinking_config,
        "tools": tool_use_context.options.tools,
        "signal": _abort_signal(tool_use_context),
        "options": {
            "get_tool_permission_context": (
                (lambda: permission_context) if permission_context is not None else None
            ),
            "model": model,
            "fast_mode": getattr(app_state, "fast_mode", None),
            "tool_choice": None,
            "is_non_interactive_session": (
                tool_use_context.options.is_non_interactive_session
            ),
            "fallback_model": fallback_model,
            "query_source": query_source,
            "agents": agent_definitions.get("activeAgents")
            if isinstance(agent_definitions, dict)
            else None,
            "allowed_agent_types": agent_definitions.get("allowedAgentTypes")
            if isinstance(agent_definitions, dict)
            else None,
            "has_append_system_prompt": bool(
                tool_use_context.options.append_system_prompt
            ),
            "max_output_tokens_override": max_output_tokens_override,
            "fetch_override": None,
            "mcp_tools": getattr(app_state, "mcp", {}).get("tools")
            if isinstance(getattr(app_state, "mcp", None), dict)
            else None,
            "has_pending_mcp_servers": any(
                getattr(client, "type", None) == "pending" for client in mcp_clients
            ),
            "query_tracking": tool_use_context.query_tracking,
            "effort_value": getattr(app_state, "effort_value", None),
            "advisor_model": getattr(app_state, "advisor_model", None),
            "skip_cache_write": skip_cache_write,
            "agent_id": tool_use_context.agent_id,
            "add_notification": tool_use_context.add_notification,
            "on_streaming_fallback": on_streaming_fallback,
            # Keep both shapes for compatibility while the API/deps layer is in flux.
            "task_budget": task_budget_payload,
            "taskBudget": task_budget_payload,
        },
    }

    try:
        async for item in _iter_call_model_result(deps.call_model(payload)):
            yield _coerce_query_yield(item)
    except NotImplementedError:
        # Production deps still contain TS-aligned stubs in this repository.
        # Keep the query loop usable while preserving the injected-deps path for
        # tests and later full API porting.
        assistant = await _call_model_fallback(
            messages=_prepare_api_messages(messages),
            system_prompt=system_prompt,
            model=model,
            tools=tool_use_context.options.tools,
            thinking_config=tool_use_context.options.thinking_config,
        )
        if assistant is not None:
            yield assistant


async def _iter_call_model_result(value: Any) -> AsyncGenerator[Any, None]:
    if hasattr(value, "__aiter__"):
        async for item in value:
            yield item
        return

    if inspect.isawaitable(value):
        value = await value

    if value is None:
        return
    if hasattr(value, "__aiter__"):
        async for item in value:
            yield item
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        for item in value:
            yield item
    else:
        yield value


def _coerce_query_yield(item: Any) -> QueryYield:
    if isinstance(
        item,
        (
            AssistantMessage,
            UserMessage,
            AttachmentMessage,
            StreamEvent,
            RequestStartEvent,
            TombstoneMessage,
            ToolUseSummaryMessage,
        ),
    ):
        return item
    if isinstance(item, dict):
        kind = item.get("type")
        if kind == "stream_event":
            return StreamEvent(event=item.get("event", item))
        if kind == "assistant":
            return AssistantMessage(
                message=APIMessage(
                    role="assistant",
                    content=item.get(
                        "content", item.get("message", {}).get("content", "")
                    ),
                    stop_reason=item.get("stop_reason"),
                    usage=item.get("usage"),
                ),
                is_api_error_message=bool(item.get("is_api_error_message", False)),
                api_error=item.get("api_error"),
            )
        if kind == "message":
            msg = item.get("message", {})
            return AssistantMessage(
                message=APIMessage(
                    role="assistant",
                    content=msg.get("content", ""),
                    stop_reason=msg.get("stop_reason"),
                    usage=msg.get("usage"),
                )
            )
        if kind == "error":
            return create_assistant_api_error_message(
                content=str(item.get("error", "API error")),
                error="api_error",
            )
    raise TypeError(f"Unsupported query yield from call_model: {item!r}")


@dataclass
class _CompactionOutcome:
    messages: Optional[list[Message]] = None
    tracking: Optional[dict[str, Any]] = None
    yielded_messages: list[Message] = field(default_factory=list)


async def _maybe_microcompact_result(
    deps: QueryDeps,
    messages: list[Message],
    tool_use_context: ToolUseContext,
    query_source: str,
) -> Any:
    try:
        result = await deps.microcompact(messages, tool_use_context, query_source)
    except TypeError:
        try:
            result = await deps.microcompact(messages, tool_use_context)
        except (NotImplementedError, TypeError, AttributeError):
            return {"messages": messages}
    except (NotImplementedError, AttributeError):
        return {"messages": messages}

    return result


def _extract_microcompact_messages(
    result: Any, fallback: list[Message]
) -> list[Message]:
    if isinstance(result, dict):
        return result.get("messages", fallback)
    return getattr(result, "messages", fallback)


def _extract_pending_cache_edits(messages_result: Any) -> Optional[_PendingCacheEdits]:
    if messages_result is None:
        return None
    compaction_info = None
    if isinstance(messages_result, dict):
        compaction_info = messages_result.get("compactionInfo") or messages_result.get(
            "compaction_info"
        )
    else:
        compaction_info = getattr(messages_result, "compactionInfo", None) or getattr(
            messages_result, "compaction_info", None
        )
    if compaction_info is None:
        return None

    pending = None
    if isinstance(compaction_info, dict):
        pending = compaction_info.get("pendingCacheEdits") or compaction_info.get(
            "pending_cache_edits"
        )
    else:
        pending = getattr(compaction_info, "pendingCacheEdits", None) or getattr(
            compaction_info, "pending_cache_edits", None
        )
    if pending is None:
        return None

    if isinstance(pending, dict):
        return _PendingCacheEdits(
            trigger=str(pending.get("trigger", "auto")),
            deleted_tool_ids=list(
                pending.get("deletedToolIds", pending.get("deleted_tool_ids", [])) or []
            ),
            baseline_cache_deleted_tokens=int(
                pending.get(
                    "baselineCacheDeletedTokens",
                    pending.get("baseline_cache_deleted_tokens", 0),
                )
                or 0
            ),
        )

    return _PendingCacheEdits(
        trigger=str(getattr(pending, "trigger", "auto")),
        deleted_tool_ids=list(
            getattr(pending, "deletedToolIds", None)
            or getattr(pending, "deleted_tool_ids", None)
            or []
        ),
        baseline_cache_deleted_tokens=int(
            getattr(pending, "baselineCacheDeletedTokens", None)
            or getattr(pending, "baseline_cache_deleted_tokens", None)
            or 0
        ),
    )


async def _maybe_autocompact(
    deps: QueryDeps,
    messages: list[Message],
    tool_use_context: ToolUseContext,
    params: QueryParams,
    tracking: Optional[dict[str, Any]],
    snip_tokens_freed: int = 0,
) -> _CompactionOutcome:
    try:
        result = await deps.autocompact(
            messages,
            tool_use_context,
            {
                "systemPrompt": params.system_prompt,
                "userContext": params.user_context,
                "systemContext": params.system_context,
                "toolUseContext": tool_use_context,
                "forkContextMessages": messages,
            },
            params.query_source,
            tracking,
            snip_tokens_freed,
        )
    except (NotImplementedError, TypeError, AttributeError):
        return _CompactionOutcome()

    compaction_result = (
        result.get("compactionResult")
        if isinstance(result, dict)
        else getattr(result, "compaction_result", None)
    )
    consecutive_failures = (
        result.get("consecutiveFailures")
        if isinstance(result, dict)
        else getattr(result, "consecutive_failures", None)
    )
    if compaction_result is None:
        if consecutive_failures is None:
            return _CompactionOutcome()
        next_tracking = dict(
            tracking or {"compacted": False, "turnId": "", "turnCounter": 0}
        )
        next_tracking["consecutiveFailures"] = consecutive_failures
        return _CompactionOutcome(tracking=next_tracking)

    post_messages = _build_post_compact_messages(compaction_result)
    return _CompactionOutcome(
        messages=post_messages,
        tracking={
            "compacted": True,
            "turnId": str(uuid4()),
            "turnCounter": 0,
            "consecutiveFailures": 0,
        },
        yielded_messages=post_messages,
    )


def _build_post_compact_messages(compaction_result: Any) -> list[Message]:
    if isinstance(compaction_result, list):
        return compaction_result
    if isinstance(compaction_result, dict):
        parts: list[Message] = []
        for key in ("summaryMessages", "attachments", "hookResults", "messages"):
            value = compaction_result.get(key)
            if isinstance(value, list):
                parts.extend(value)
        return parts
    parts = []
    for attr in ("summary_messages", "attachments", "hook_results", "messages"):
        value = getattr(compaction_result, attr, None)
        if isinstance(value, list):
            parts.extend(value)
    return parts


async def _run_stop_hooks(
    *,
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    params: QueryParams,
    tool_use_context: ToolUseContext,
    stop_hook_active: Optional[bool],
) -> AsyncGenerator[QueryYield, None]:
    try:
        async for msg in handle_stop_hooks(
            messages_for_query,
            assistant_messages,
            params.system_prompt,
            params.user_context,
            params.system_context,
            tool_use_context,
            params.query_source,
            stop_hook_active,
        ):
            yield msg
    except Exception as error:  # noqa: BLE001
        log_error(error)
        yield create_system_message(f"Stop hook failed: {error}", "warning")


async def _run_stop_hooks_collect(
    *,
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    params: QueryParams,
    tool_use_context: ToolUseContext,
    stop_hook_active: Optional[bool],
) -> dict[str, Any]:
    yielded: list[QueryYield] = []
    blocking_errors: list[Message] = []
    prevent = False
    async for msg in _run_stop_hooks(
        messages_for_query=messages_for_query,
        assistant_messages=assistant_messages,
        params=params,
        tool_use_context=tool_use_context,
        stop_hook_active=stop_hook_active,
    ):
        yielded.append(msg)
        if (
            getattr(msg, "type", None) == "attachment"
            and getattr(msg, "attachment", {}).get("type")
            == "hook_stopped_continuation"
        ):
            prevent = True
        if getattr(msg, "type", None) == "user" and getattr(msg, "is_meta", False):
            blocking_errors.append(msg)
    return {
        "messages": yielded,
        "blocking_errors": blocking_errors,
        "prevent_continuation": prevent,
    }


async def _run_tool_updates(
    tool_use_blocks: list[dict[str, Any]],
    assistant_messages: list[AssistantMessage],
    can_use_tool: CanUseToolFn,
    tool_use_context: ToolUseContext,
) -> AsyncGenerator[_ToolUpdate, None]:
    try:
        async for update in run_tools(
            tool_use_blocks,
            assistant_messages,
            can_use_tool,
            tool_use_context,
        ):
            yield _ToolUpdate(
                message=update.message,
                new_context=update.new_context,
            )
    except NotImplementedError:
        async for update in _run_tools_fallback(
            tool_use_blocks,
            assistant_messages,
            can_use_tool,
            tool_use_context,
        ):
            yield update


async def _run_tool_updates_with_streaming_executor(
    streaming_executor: Any,
) -> AsyncGenerator[_ToolUpdate, None]:
    async for update in streaming_executor.get_remaining_results():
        yield _ToolUpdate(
            message=update.message,
            new_context=update.new_context,
        )


async def _run_tools_fallback(
    tool_use_blocks: list[dict[str, Any]],
    assistant_messages: list[AssistantMessage],
    can_use_tool: CanUseToolFn,
    tool_use_context: ToolUseContext,
) -> AsyncGenerator[_ToolUpdate, None]:
    current_context = tool_use_context
    for block in tool_use_blocks:
        tool_name = str(block.get("name", ""))
        tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
        tool_use_id = str(block.get("id") or uuid4())
        assistant = _owning_assistant(assistant_messages, tool_use_id)
        tool = find_tool_by_name(current_context.options.tools, tool_name)

        if tool is None:
            yield _ToolUpdate(
                message=create_user_message(
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": f"Tool {tool_name} not found",
                            "is_error": True,
                        }
                    ],
                    tool_use_result=f"Tool {tool_name} not found",
                    source_tool_assistant_uuid=getattr(assistant, "uuid", None),
                ),
                new_context=current_context,
            )
            continue

        try:
            permission = await can_use_tool(
                tool,
                tool_input,
                current_context,
                assistant,
                tool_use_id,
                None,
            )
            if getattr(permission, "behavior", "allow") == "deny":
                reason = getattr(permission, "message", "Permission denied")
                yield _ToolUpdate(
                    message=create_user_message(
                        content=[
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": reason,
                                "is_error": True,
                            }
                        ],
                        tool_use_result=reason,
                        source_tool_assistant_uuid=getattr(assistant, "uuid", None),
                    ),
                    new_context=current_context,
                )
                continue

            result = await tool.call(
                tool_input, current_context, can_use_tool, assistant
            )
            if result.context_modifier is not None:
                current_context = result.context_modifier(current_context)
            block_param = tool.map_tool_result_to_tool_result_block_param(
                result.data,
                tool_use_id,
            )
            yield _ToolUpdate(
                message=create_user_message(
                    content=[block_param],
                    tool_use_result=str(result.data),
                    source_tool_assistant_uuid=getattr(assistant, "uuid", None),
                ),
                new_context=current_context,
            )
        except Exception as error:  # noqa: BLE001
            yield _ToolUpdate(
                message=create_user_message(
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": f"Error: {error}",
                            "is_error": True,
                        }
                    ],
                    tool_use_result=f"Error: {error}",
                    source_tool_assistant_uuid=getattr(assistant, "uuid", None),
                ),
                new_context=current_context,
            )


def _owning_assistant(
    assistant_messages: list[AssistantMessage],
    tool_use_id: str,
) -> AssistantMessage:
    for assistant in assistant_messages:
        for block in _extract_tool_use_blocks(assistant):
            if block.get("id") == tool_use_id:
                return assistant
    return assistant_messages[-1] if assistant_messages else AssistantMessage()


async def _yield_missing_tool_result_blocks(
    assistant_messages: list[AssistantMessage],
    error_message: str,
) -> AsyncGenerator[UserMessage, None]:
    for assistant_message in assistant_messages:
        for tool_use in _extract_tool_use_blocks(assistant_message):
            yield create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "content": error_message,
                        "is_error": True,
                        "tool_use_id": tool_use.get("id", ""),
                    }
                ],
                tool_use_result=error_message,
                source_tool_assistant_uuid=assistant_message.uuid,
            )


async def _yield_missing_tool_result_blocks_for_unresolved(
    assistant_messages: list[AssistantMessage],
    tool_results: list[UserMessage | AttachmentMessage],
    error_message: str,
) -> AsyncGenerator[UserMessage, None]:
    resolved_ids = _resolved_tool_use_ids(tool_results)
    for assistant_message in assistant_messages:
        for tool_use in _extract_tool_use_blocks(assistant_message):
            tool_use_id = str(tool_use.get("id", ""))
            if tool_use_id in resolved_ids:
                continue
            yield create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "content": error_message,
                        "is_error": True,
                        "tool_use_id": tool_use_id,
                    }
                ],
                tool_use_result=error_message,
                source_tool_assistant_uuid=assistant_message.uuid,
            )


def _resolved_tool_use_ids(
    tool_results: list[UserMessage | AttachmentMessage],
) -> set[str]:
    resolved: set[str] = set()
    for msg in tool_results:
        if msg.type != "user":
            continue
        content = msg.message.content
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(block.get("tool_use_id"), str)
            ):
                resolved.add(block["tool_use_id"])
    return resolved


def _estimate_context_tokens_from_messages(messages: list[Message]) -> int:
    raw = "".join(str(getattr(m, "message", "")) for m in messages)
    try:
        return max(0, token_count_with_estimation(raw))
    except Exception:
        return 0


def _apply_task_budget_spend(
    remaining: Optional[int],
    total: int,
    messages_for_query: list[Message],
) -> int:
    """Decrement task budget remaining by the pre-compact context window.

    Mirrors TS L504-515: uses finalContextTokensFromLastResponse to read
    the API-reported iterations[-1] as the authoritative final window,
    falling back to local estimation when API usage is unavailable.
    """
    if total <= 0:
        return remaining if remaining is not None else 0
    baseline = remaining if remaining is not None else total
    if baseline <= 0:
        return 0
    # Use API-reported final context when available (TS L509-510)
    api_context = final_context_tokens_from_last_response(messages_for_query)
    if api_context > 0:
        return max(0, baseline - api_context)
    # Fall back to local estimation
    used = _estimate_context_tokens_from_messages(messages_for_query)
    return max(0, baseline - used)


def _build_task_budget_payload(
    task_budget: Optional[dict[str, float]],
    task_budget_remaining: Optional[int],
) -> Optional[dict[str, float]]:
    if not task_budget:
        return None
    total = float(task_budget.get("total", 0))
    payload: dict[str, float] = {"total": total}
    if task_budget_remaining is not None:
        payload["remaining"] = float(max(0, task_budget_remaining))
    return payload


def _extract_tool_use_blocks(message: AssistantMessage) -> list[dict[str, Any]]:
    content = getattr(message.message, "content", None)
    if not isinstance(content, list):
        return []
    return [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _backfill_tool_use_inputs_for_yield(
    message: QueryYield,
    tools: Sequence[Tool],
) -> QueryYield:
    if not isinstance(message, AssistantMessage):
        return message

    content = getattr(message.message, "content", None)
    if not isinstance(content, list):
        return message

    cloned_content: Optional[list[Any]] = None
    for i, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        input_value = block.get("input")
        if not isinstance(input_value, dict):
            continue
        tool = find_tool_by_name(tools, str(block.get("name", "")))
        backfill = getattr(tool, "backfill_observable_input", None) if tool else None
        if backfill is None:
            backfill = getattr(tool, "backfillObservableInput", None) if tool else None
        if not callable(backfill):
            continue
        input_copy = dict(input_value)
        try:
            backfill(input_copy)
        except Exception:
            continue
        added_fields = any(key not in input_value for key in input_copy.keys())
        if not added_fields:
            continue
        if cloned_content is None:
            cloned_content = list(content)
        cloned_content[i] = {**block, "input": input_copy}

    if cloned_content is None:
        return message

    return AssistantMessage(
        type=message.type,
        uuid=message.uuid,
        timestamp=message.timestamp,
        message=APIMessage(
            role=message.message.role,
            content=cloned_content,
            stop_reason=message.message.stop_reason,
            usage=message.message.usage,
        ),
        cost_usd=message.cost_usd,
        duration_ms=message.duration_ms,
        is_api_error_message=message.is_api_error_message,
        api_error=message.api_error,
    )


def _is_withheld_max_output_tokens(
    msg: Optional[Message | StreamEvent],
) -> bool:
    return isinstance(msg, AssistantMessage) and msg.api_error == "max_output_tokens"


def _is_withheld_prompt_too_long(msg: Optional[Message | StreamEvent]) -> bool:
    return _is_prompt_too_long_message(msg)


def _is_withheld_media_error(msg: Optional[Message | StreamEvent]) -> bool:
    return _is_media_size_error_message(msg)


def _should_withhold_assistant_error(msg: Optional[Message | StreamEvent]) -> bool:
    return (
        _is_withheld_max_output_tokens(msg)
        or _is_withheld_prompt_too_long(msg)
        or _is_withheld_media_error(msg)
    )


def _is_fallback_triggered_error(error: Exception) -> bool:
    if type(error).__name__ == "FallbackTriggeredError":
        return True
    msg = str(error).lower()
    return "fallback" in msg and "trigger" in msg


def _is_prompt_too_long_message(msg: Optional[Message | StreamEvent]) -> bool:
    if not isinstance(msg, AssistantMessage):
        return False
    if msg.api_error in ("prompt_too_long", "invalid_request"):
        return True
    text = _assistant_text(msg).lower()
    return "prompt too long" in text or "too many tokens" in text


def _is_media_size_error_message(msg: Optional[Message | StreamEvent]) -> bool:
    if not isinstance(msg, AssistantMessage):
        return False
    if msg.api_error in ("image_error", "media_too_large"):
        return True
    text = _assistant_text(msg).lower()
    return "image" in text and ("too large" in text or "exceeds" in text)


def _assistant_text(msg: AssistantMessage) -> str:
    content = getattr(msg.message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _does_most_recent_assistant_message_exceed_200k(messages: list[Message]) -> bool:
    for msg in reversed(messages):
        if getattr(msg, "type", None) != "assistant":
            continue
        usage = getattr(getattr(msg, "message", None), "usage", None)
        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens")
            if isinstance(input_tokens, int):
                return input_tokens > 200_000
        try:
            return token_count_with_estimation(_assistant_text(msg)) > 200_000
        except Exception:
            return False
    return False


def _filter_duplicate_memory_attachments(
    attachments: list[dict[str, Any]],
    read_file_state: Any,
) -> list[dict[str, Any]]:
    """Filter out memory attachments already tracked in readFileState.

    Port of: src/utils/attachments.ts filterDuplicateMemoryAttachments (L2520-2540).

    For each relevant_memories attachment, removes memories whose path is
    already known to readFileState (read/written/edited). Adds remaining
    memories to readFileState. Returns only attachments with >0 unfiltered
    memories.
    """
    filtered: list[dict[str, Any]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("type") != "relevant_memories":
            filtered.append(attachment)
            continue

        memories = attachment.get("memories", [])
        if not isinstance(memories, list):
            continue

        kept: list[dict[str, Any]] = []
        for m in memories:
            if not isinstance(m, dict):
                continue
            path = m.get("path", "")
            if hasattr(read_file_state, "has") and read_file_state.has(path):
                continue
            kept.append(m)
            # Track as read to prevent future duplicates (TS L2531-2536)
            if hasattr(read_file_state, "set"):
                try:
                    read_file_state.set(
                        path,
                        {
                            "content": m.get("content", ""),
                            "timestamp": m.get("mtimeMs", 0),
                            "offset": None,
                            "limit": m.get("limit"),
                        },
                    )
                except Exception:
                    pass

        if kept:
            filtered.append({**attachment, "memories": kept})

    return filtered


def _is_at_blocking_limit(
    messages: list[Message],
    tool_use_context: Any = None,
) -> bool:
    """Check token count against model-specific blocking threshold.

    Port of: query.ts L636-638 calculateTokenWarningState with model-aware check.

    Uses model-specific thresholds when available; falls back to 195k for
    unknown models (matching TS behavior for the most common models).
    """
    raw = "".join(str(getattr(m, "message", "")) for m in messages)
    try:
        token_count = token_count_with_estimation(raw)
    except Exception:
        return False

    # Resolve model-specific threshold (matching TS calculateTokenWarningState)
    threshold = _get_blocking_threshold(tool_use_context)
    return token_count >= threshold


def _get_blocking_threshold(tool_use_context: Any = None) -> int:
    """Get model-specific token blocking threshold.

    Matching TS calculateTokenWarningState: different Claude models have
    different context windows, and the blocking threshold varies accordingly.
    Falls back to 195k for unknown models.
    """
    model = "unknown"
    if tool_use_context is not None:
        options = getattr(tool_use_context, "options", None)
        if options is not None:
            model = str(getattr(options, "main_loop_model", "unknown") or "unknown")

    # Model-specific thresholds (input context windows minus safety margin)
    if model.startswith("claude-sonnet-4-") or model.startswith("claude-opus-4-"):
        return 195_000  # 200k context
    if "sonnet" in model or "haiku" in model:
        return 195_000  # 200k context
    if "opus" in model:
        return 195_000  # 200k context
    # Default conservative threshold for unknown models
    return 195_000


def _should_run_blocking_limit_precheck(query_source: str) -> bool:
    """Block if we've hit the hard blocking limit (TS L592-648).

    Skips when automatic recovery paths (reactive compact, context collapse)
    are available — they need real API errors, not synthetic preempts.
    Also skips for compact/session_memory forks that would deadlock.
    """
    if query_source in ("compact", "session_memory"):
        return False

    auto_enabled = _is_auto_compact_enabled()

    # collapseOwnsIt: context-collapse is enabled AND auto-compact is on
    collapse_owns_it = False
    if feature("CONTEXT_COLLAPSE"):
        collapse_owns_it = _context_collapse_enabled() and auto_enabled

    # Skip if reactiveCompact+autoCompact (RC handles recovery)
    # Skip if collapseOwnsIt (collapse handles recovery)
    if (_reactive_compact_enabled() and auto_enabled) or collapse_owns_it:
        return False
    return True


def _is_auto_compact_enabled() -> bool:
    """Check if auto-compact is enabled (mirrors TS isAutoCompactEnabled)."""
    import os

    val = os.environ.get("DISABLE_COMPACT", "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return False
    val2 = os.environ.get("DISABLE_AUTO_COMPACT", "").strip().lower()
    if val2 in {"1", "true", "yes", "on"}:
        return False
    try:
        from hare.utils.config import get_global_config

        return get_global_config().auto_compact_enabled
    except Exception:
        return True


def _reactive_compact_enabled() -> bool:
    try:
        import importlib

        reactive = importlib.import_module("hare.services.compact.reactive_compact")
        checker = getattr(reactive, "is_reactive_compact_enabled", None)
        if callable(checker):
            return bool(checker())
        return True
    except Exception:
        return False


def _context_collapse_enabled() -> bool:
    try:
        import importlib

        collapse = importlib.import_module("hare.services.context_collapse")
        checker = getattr(collapse, "is_context_collapse_enabled", None)
        if callable(checker):
            return bool(checker())
        return True
    except Exception:
        return False


def _snapshot_queued_commands(
    query_source: str,
    current_agent_id: Optional[str],
    allowed_priorities: tuple[str, ...] = ("now", "next"),
) -> list[Any]:
    """Snapshot matching commands from the queue (TS L1570-1578).

    Snapshots without removing — removal happens after attachment processing.
    """
    queue = get_command_queue()
    if not queue:
        return []
    is_main_thread = (
        query_source.startswith("repl_main_thread") or query_source == "sdk"
    )
    return [
        cmd
        for cmd in queue
        if (
            not is_slash_command(cmd)
            and getattr(cmd, "mode", "prompt") in ("prompt", "task-notification")
            and getattr(cmd, "priority", "next") in allowed_priorities
            and (
                (is_main_thread and getattr(cmd, "agent_id", None) is None)
                or (
                    (not is_main_thread)
                    and getattr(cmd, "mode", "prompt") == "task-notification"
                    and getattr(cmd, "agent_id", None) == current_agent_id
                )
            )
        )
    ]


def _command_to_attachment(cmd: Any) -> Optional[AttachmentMessage]:
    """Convert a queued command to an attachment message."""
    payload = {
        "type": "queued_command",
        "mode": getattr(cmd, "mode", "prompt"),
        "value": getattr(cmd, "value", ""),
        "isMeta": bool(getattr(cmd, "is_meta", False)),
    }
    return create_attachment_message(payload)


def _remove_consumed_commands(commands: list[Any]) -> None:
    """Remove consumed commands from the shared queue (TS L1642)."""
    if not commands:
        return
    try:
        queue = get_command_queue()
        if queue:
            uuids_to_remove = set()
            for cmd in commands:
                uuid = getattr(cmd, "uuid", None)
                if uuid:
                    uuids_to_remove.add(uuid)
            if uuids_to_remove:
                remaining = [
                    c for c in queue if getattr(c, "uuid", None) not in uuids_to_remove
                ]
                queue.clear()
                queue.extend(remaining)
    except (AttributeError, Exception):
        pass


def _maybe_create_streaming_tool_executor(
    enabled: bool,
    tool_use_context: ToolUseContext,
    can_use_tool: CanUseToolFn,
) -> Any:
    if not enabled:
        return None
    try:
        return StreamingToolExecutor(
            list(tool_use_context.options.tools),
            can_use_tool,
            tool_use_context,
        )
    except Exception:
        return None


def _streaming_executor_add_tools(
    executor: Any,
    tool_use_blocks: list[dict[str, Any]],
    assistant_message: AssistantMessage,
) -> None:
    if executor is None:
        return
    for block in tool_use_blocks:
        try:
            executor.add_tool(block, assistant_message)
        except Exception:
            continue


def _streaming_executor_get_completed_results(executor: Any) -> list[Any]:
    if executor is None:
        return []
    try:
        return list(executor.get_completed_results())
    except Exception:
        return []


async def _streaming_executor_get_remaining_results(executor: Any) -> AsyncGenerator:
    """Consume remaining tool results from streaming executor (TS L1019)."""
    if executor is None:
        return
    try:
        if hasattr(executor, "get_remaining_results"):
            async for update in executor.get_remaining_results():
                yield update
    except Exception:
        return


def _maybe_chicago_mcp_cleanup(tool_use_context: ToolUseContext) -> None:
    """CHICAGO_MCP auto-unhide + lock release on interrupt (TS L1033-1042).

    Main thread only — subagents don't start CU sessions.
    Failures are silent — this is dogfooding cleanup, not critical path.
    """
    if not feature("CHICAGO_MCP"):
        return
    if tool_use_context.agent_id:
        return
    try:
        import importlib

        cleanup_mod = importlib.import_module("hare.utils.computer_use.cleanup")
        if hasattr(cleanup_mod, "cleanup_computer_use_after_turn"):
            import asyncio

            asyncio.ensure_future(
                cleanup_mod.cleanup_computer_use_after_turn(tool_use_context)
            )
    except (ImportError, AttributeError, Exception):
        pass


def _reset_streaming_executor(
    executor: Any,
    enabled: bool,
    tool_use_context: ToolUseContext,
    can_use_tool: CanUseToolFn,
) -> Any:
    if executor is not None:
        try:
            if hasattr(executor, "discard"):
                executor.discard()
        except Exception:
            pass
    return _maybe_create_streaming_tool_executor(
        enabled,
        tool_use_context,
        can_use_tool,
    )


def _schedule_tool_use_summary(
    *,
    enabled: bool,
    tool_use_blocks: list[dict[str, Any]],
    tool_results: list[UserMessage | AttachmentMessage],
    tool_use_context: ToolUseContext,
    assistant_messages: list[AssistantMessage],
) -> Optional[asyncio.Task[ToolUseSummaryMessage | None]]:
    if (
        not enabled
        or not tool_use_blocks
        or _is_aborted(tool_use_context)
        or bool(tool_use_context.agent_id)
    ):
        return None

    async def _build() -> ToolUseSummaryMessage | None:
        try:
            tool_uses = _collect_tool_uses_for_summary(
                tool_use_blocks,
                tool_results,
                _extract_last_assistant_text(assistant_messages),
            )
            summary = generate_tool_use_summary(tool_uses)
            tool_ids = [
                str(block.get("id", ""))
                for block in tool_use_blocks
                if block.get("id") is not None
            ]
            return create_tool_use_summary_message(summary, tool_ids)
        except Exception:
            return None

    return asyncio.create_task(_build())


async def _resolve_pending_tool_use_summary(
    pending: Any,
) -> Optional[ToolUseSummaryMessage]:
    try:
        if isinstance(pending, asyncio.Task):
            return await pending
        if inspect.isawaitable(pending):
            return await pending
        if isinstance(pending, ToolUseSummaryMessage):
            return pending
    except Exception:
        return None
    return None


def _cancel_pending_tool_use_summary(pending: Any) -> None:
    if isinstance(pending, asyncio.Task) and not pending.done():
        pending.cancel()


def _collect_tool_uses_for_summary(
    tool_use_blocks: list[dict[str, Any]],
    tool_results: list[UserMessage | AttachmentMessage],
    last_assistant_text: Optional[str] = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in tool_use_blocks:
        tid = str(block.get("id", ""))
        status = "completed"
        for result in tool_results:
            if result.type != "user":
                continue
            content = result.message.content
            if not isinstance(content, list):
                continue
            for c in content:
                if (
                    isinstance(c, dict)
                    and c.get("type") == "tool_result"
                    and str(c.get("tool_use_id", "")) == tid
                    and c.get("is_error")
                ):
                    status = "error"
        out.append(
            {
                "id": tid,
                "name": str(block.get("name", "")),
                "input": block.get("input", {}),
                "status": status,
                "last_assistant_text": last_assistant_text,
            }
        )
    return out


def _extract_last_assistant_text(
    assistant_messages: list[AssistantMessage],
) -> Optional[str]:
    if not assistant_messages:
        return None
    content = getattr(getattr(assistant_messages[-1], "message", None), "content", None)
    if not isinstance(content, list):
        return None
    last_text: Optional[str] = None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                last_text = text
    return last_text


def _start_memory_prefetch_once(
    messages: list[Message],
    tool_use_context: ToolUseContext,
) -> Optional[_PendingPrefetch]:
    try:
        from hare.utils.attachments import start_relevant_memory_prefetch

        prefetch = start_relevant_memory_prefetch(messages, tool_use_context)
        if hasattr(prefetch, "promise"):
            promise = getattr(prefetch, "promise")
            if isinstance(promise, asyncio.Task):
                return _PendingPrefetch(task=promise)
            if inspect.isawaitable(promise):
                return _PendingPrefetch(task=asyncio.create_task(promise))
        if inspect.isawaitable(prefetch):
            return _PendingPrefetch(task=asyncio.create_task(prefetch))
    except Exception:
        return None
    return None


def _start_skill_prefetch_for_turn(
    messages: list[Message],
    tool_use_context: ToolUseContext,
) -> Any:
    try:
        from hare.services.skill_search import prefetch as skill_prefetch  # type: ignore

        return skill_prefetch.start_skill_discovery_prefetch(
            None, messages, tool_use_context
        )
    except Exception:
        return None


async def _consume_memory_prefetch_if_ready(
    pending: Optional[_PendingPrefetch],
    tool_use_context: ToolUseContext,
    turn_count: int,
) -> list[AttachmentMessage]:
    if pending is None:
        return []
    if pending.consumed_on_iteration != -1:
        return []
    if not pending.task.done():
        return []
    try:
        memory_attachments = await pending.task
    except Exception:
        return []
    pending.consumed_on_iteration = turn_count - 1
    if not isinstance(memory_attachments, list):
        return []

    # TS query.ts:1603 — filter duplicates via readFileState
    read_file_state = getattr(tool_use_context, "read_file_state", None)
    if read_file_state is not None and hasattr(read_file_state, "has"):
        memory_attachments = _filter_duplicate_memory_attachments(
            memory_attachments, read_file_state
        )

    result: list[AttachmentMessage] = []
    for att in memory_attachments:
        if isinstance(att, dict):
            result.append(create_attachment_message(att))
    return result


async def _collect_skill_prefetch(
    pending_skill_prefetch: Any,
) -> list[AttachmentMessage]:
    if pending_skill_prefetch is None:
        return []
    try:
        from hare.services.skill_search import prefetch as skill_prefetch  # type: ignore

        out = await skill_prefetch.collect_skill_discovery_prefetch(
            pending_skill_prefetch
        )
        if not isinstance(out, list):
            return []
        return [create_attachment_message(att) for att in out if isinstance(att, dict)]
    except Exception:
        return []


async def _try_reactive_recovery(
    *,
    state: _State,
    params: QueryParams,
    tool_use_context: ToolUseContext,
    messages_for_query: list[Message],
    tracking: Optional[dict[str, Any]],
    task_budget_remaining: Optional[int],
    mark_transition: Callable[[Continue], Continue],
) -> Optional[tuple[_State, Optional[int], list[Message]]]:
    log_event(
        "tengu_reactive_recovery_attempted",
        {"query_source": params.query_source, "outcome": "attempted"},
    )
    if state.has_attempted_reactive_compact:
        log_event(
            "tengu_reactive_recovery_skipped",
            {
                "reason": "already_attempted",
                "query_source": params.query_source,
                "outcome": "skipped",
            },
        )
        return None
    try:
        import importlib

        reactive = importlib.import_module("hare.services.compact.reactive_compact")
    except Exception:
        log_event(
            "tengu_reactive_recovery_skipped",
            {
                "reason": "module_unavailable",
                "query_source": params.query_source,
                "outcome": "skipped",
            },
        )
        return None

    try:
        compacted = await reactive.try_reactive_compact(  # type: ignore[attr-defined]
            {
                "hasAttempted": state.has_attempted_reactive_compact,
                "querySource": params.query_source,
                "aborted": _is_aborted(tool_use_context),
                "messages": messages_for_query,
                "cacheSafeParams": {
                    "systemPrompt": params.system_prompt,
                    "userContext": params.user_context,
                    "systemContext": params.system_context,
                    "toolUseContext": tool_use_context,
                    "forkContextMessages": messages_for_query,
                },
            }
        )
    except Exception:
        log_event(
            "tengu_reactive_recovery_failed",
            {
                "reason": "runtime_error",
                "query_source": params.query_source,
                "outcome": "failed",
            },
        )
        return None

    if not compacted:
        log_event(
            "tengu_reactive_recovery_failed",
            {
                "reason": "no_compaction",
                "query_source": params.query_source,
                "outcome": "failed",
            },
        )
        return None

    if params.task_budget:
        before_budget = (
            task_budget_remaining
            if task_budget_remaining is not None
            else int(params.task_budget.get("total", 0))
        )
        task_budget_remaining = _apply_task_budget_spend(
            task_budget_remaining,
            int(params.task_budget.get("total", 0)),
            messages_for_query,
        )
        log_event(
            "tengu_task_budget_decremented",
            {
                "path": "reactive_compact",
                "before": before_budget,
                "after": task_budget_remaining,
                "spent": max(0, before_budget - (task_budget_remaining or 0)),
                "query_source": params.query_source,
            },
        )

    post_compact_messages = _build_post_compact_messages(compacted)
    next_state = replace(
        state,
        messages=post_compact_messages,
        tool_use_context=tool_use_context,
        auto_compact_tracking=None,  # TS: explicitly set to undefined (query.ts:1154)
        has_attempted_reactive_compact=True,
        max_output_tokens_override=None,
        pending_tool_use_summary=None,
        stop_hook_active=None,
        transition=mark_transition(Continue(reason="reactive_compact_retry")),
    )
    log_event(
        "tengu_reactive_recovery_compacted",
        {"query_source": params.query_source, "outcome": "compacted"},
    )
    return next_state, task_budget_remaining, post_compact_messages


async def _try_collapse_drain_recovery(
    *,
    state: _State,
    params: QueryParams,
    tool_use_context: ToolUseContext,
    messages_for_query: list[Message],
    mark_transition: Callable[[Continue], Continue],
) -> Optional[_State]:
    log_event(
        "tengu_collapse_drain_recovery_attempted",
        {"query_source": params.query_source, "outcome": "attempted"},
    )
    try:
        import importlib

        collapse = importlib.import_module("hare.services.context_collapse")
    except Exception:
        log_event(
            "tengu_collapse_drain_recovery_skipped",
            {
                "reason": "module_unavailable",
                "query_source": params.query_source,
                "outcome": "skipped",
            },
        )
        return None

    # Mirror TS guard: if previous transition already drained once, skip repeat drain.
    if state.transition and state.transition.reason == "collapse_drain_retry":
        log_event(
            "tengu_collapse_drain_recovery_skipped",
            {
                "reason": "already_drained",
                "query_source": params.query_source,
                "outcome": "skipped",
            },
        )
        return None

    try:
        drained = collapse.recover_from_overflow(  # type: ignore[attr-defined]
            messages_for_query,
            params.query_source,
        )
    except Exception:
        log_event(
            "tengu_collapse_drain_recovery_failed",
            {
                "reason": "runtime_error",
                "query_source": params.query_source,
                "outcome": "failed",
            },
        )
        return None

    if not drained:
        log_event(
            "tengu_collapse_drain_recovery_failed",
            {
                "reason": "no_drain_result",
                "query_source": params.query_source,
                "outcome": "failed",
            },
        )
        return None

    committed = 0
    if isinstance(drained, dict):
        committed = int(drained.get("committed", 0))
        drained_messages = drained.get("messages", messages_for_query)
    else:
        committed = int(getattr(drained, "committed", 0))
        drained_messages = getattr(drained, "messages", messages_for_query)

    if committed <= 0:
        log_event(
            "tengu_collapse_drain_recovery_failed",
            {
                "reason": "no_commits",
                "query_source": params.query_source,
                "outcome": "failed",
            },
        )
        return None

    log_event(
        "tengu_collapse_drain_recovery_compacted",
        {
            "query_source": params.query_source,
            "committed": committed,
            "outcome": "compacted",
        },
    )
    return replace(
        state,
        messages=list(drained_messages),
        tool_use_context=tool_use_context,
        max_output_tokens_override=None,
        pending_tool_use_summary=None,
        stop_hook_active=None,
        transition=mark_transition(
            Continue(reason="collapse_drain_retry", committed=committed)
        ),
    )


def _next_query_tracking(
    tool_use_context: ToolUseContext, deps: QueryDeps
) -> QueryChainTracking:
    if tool_use_context.query_tracking is not None:
        return QueryChainTracking(
            chain_id=tool_use_context.query_tracking.chain_id,
            depth=tool_use_context.query_tracking.depth + 1,
        )
    return QueryChainTracking(chain_id=deps.uuid(), depth=0)


def _current_model(tool_use_context: ToolUseContext, messages: list[Message]) -> str:
    permission_mode = "default"
    if tool_use_context.get_app_state is not None:
        try:
            app_state = tool_use_context.get_app_state()
            permission_ctx = getattr(app_state, "tool_permission_context", None)
            permission_mode = getattr(permission_ctx, "mode", permission_mode)
        except Exception:
            permission_mode = "default"

    return get_runtime_main_loop_model(
        permission_mode=permission_mode,
        main_loop_model=(
            tool_use_context.options.main_loop_model or "claude-sonnet-4-20250514"
        ),
        exceeds_200k_tokens=(
            permission_mode == "plan"
            and _does_most_recent_assistant_message_exceed_200k(messages)
        ),
    )


def _full_system_prompt(
    system_prompt: list[str], system_context: dict[str, str]
) -> list[str]:
    if not system_context:
        return list(system_prompt)
    context_lines = [f"{key}: {value}" for key, value in system_context.items()]
    return [*system_prompt, "\n".join(context_lines)]


def _prepend_user_context(
    messages: list[Message], user_context: dict[str, str]
) -> list[Message]:
    if not user_context:
        return messages
    context = "\n".join(f"{key}: {value}" for key, value in user_context.items())
    return [create_user_message(content=context, is_meta=True), *messages]


def _prepare_api_messages(messages: list[Message]) -> list[dict[str, Any]]:
    api_msgs: list[dict[str, Any]] = []
    for msg in messages:
        if getattr(msg, "type", None) in ("user", "assistant"):
            api_msgs.append(
                {
                    "role": msg.message.role,
                    "content": msg.message.content,
                }
            )
    return api_msgs


async def _call_model_fallback(
    *,
    messages: list[dict[str, Any]],
    system_prompt: list[str],
    model: str,
    tools: Sequence[Tool],
    thinking_config: Optional[dict[str, Any]] = None,
) -> Optional[AssistantMessage]:
    try:
        from hare.services.api.client import call_model_api

        return await call_model_api(
            messages=messages,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
            thinking_config=thinking_config,
            stream=False,
        )
    except ImportError:
        return AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {
                        "type": "text",
                        "text": (
                            "No API client configured. Install with: "
                            "pip install hare[anthropic]"
                        ),
                    }
                ],
                stop_reason="end_turn",
            )
        )


def _abort_signal(tool_use_context: ToolUseContext) -> Any:
    controller = tool_use_context.abort_controller
    return getattr(controller, "signal", controller)


def _is_aborted(tool_use_context: ToolUseContext) -> bool:
    controller = tool_use_context.abort_controller
    if controller is None:
        return False
    signal = getattr(controller, "signal", None)
    if signal is not None:
        return bool(getattr(signal, "aborted", False))
    if isinstance(controller, asyncio.Event):
        return controller.is_set()
    return bool(getattr(controller, "aborted", False))


def _abort_reason(tool_use_context: ToolUseContext) -> Optional[str]:
    controller = tool_use_context.abort_controller
    if controller is None:
        return None
    signal = getattr(controller, "signal", None)
    if signal is not None:
        reason = getattr(signal, "reason", None)
        return str(reason) if reason is not None else None
    reason = getattr(controller, "reason", None)
    return str(reason) if reason is not None else None


async def _allow_tool(*_args: Any, **_kwargs: Any) -> Any:
    class _Allowed:
        behavior = "allow"

    return _Allowed()


def _get_current_turn_token_budget() -> Optional[int]:
    try:
        from hare.bootstrap.state import get_current_turn_token_budget

        return get_current_turn_token_budget()
    except (ImportError, AttributeError):
        return None


def _get_turn_output_tokens() -> int:
    try:
        from hare.bootstrap.state import get_turn_output_tokens

        return get_turn_output_tokens()
    except (ImportError, AttributeError):
        return 0


def _increment_budget_continuation_count() -> None:
    try:
        from hare.bootstrap.state import increment_budget_continuation_count

        increment_budget_continuation_count()
    except (ImportError, AttributeError):
        return


def _snapshot_output_tokens_for_turn(
    task_budget: Optional[dict[str, float]],
) -> None:
    budget: Optional[int] = None
    if task_budget is not None:
        total = task_budget.get("total")
        if isinstance(total, (int, float)):
            budget = int(total)
    try:
        from hare.bootstrap.state import snapshot_output_tokens_for_turn

        snapshot_output_tokens_for_turn(budget)
    except (ImportError, AttributeError):
        return


# ---------------------------------------------------------------------------
# Feature-gated helpers (TS feature() pattern, ported as importlib guards)
# ---------------------------------------------------------------------------


def _execute_post_sampling_hooks(
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    params: QueryParams,
    tool_use_context: ToolUseContext,
) -> None:
    """Fire-and-forget post-sampling hooks after model streaming completes.

    Mirrors TS L999-1009: executePostSamplingHooks is called after
    assistant_messages are collected, before needsFollowUp check.
    Hooks run asynchronously — failures are silent.
    """
    try:
        from hare.utils.hooks.post_sampling_hooks import run_post_sampling_hooks

        ctx: dict[str, Any] = {
            "messages": [*messages_for_query, *assistant_messages],
            "systemPrompt": params.system_prompt,
            "userContext": params.user_context,
            "systemContext": params.system_context,
            "toolUseContext": tool_use_context,
            "querySource": params.query_source,
        }
        asyncio.ensure_future(run_post_sampling_hooks(ctx))
    except (ImportError, AttributeError):
        return


def _maybe_snip_compact(
    messages_for_query: list[Message],
) -> tuple[list[Message], int, Optional[Any]]:
    """Apply HISTORY_SNIP compaction if feature-gated and module available.

    Returns (messages, snip_tokens_freed, boundary_message_or_none).
    """
    if not feature("HISTORY_SNIP"):
        return messages_for_query, 0, None
    try:
        import importlib

        snip = importlib.import_module("hare.services.compact.snip_compact")
        if hasattr(snip, "snip_compact_if_needed"):
            result = snip.snip_compact_if_needed(messages_for_query)
            if isinstance(result, dict):
                return (
                    result.get("messages", messages_for_query),
                    result.get("tokensFreed", 0),
                    result.get("boundaryMessage"),
                )
            if hasattr(result, "messages"):
                return (
                    getattr(result, "messages", messages_for_query),
                    getattr(result, "tokens_freed", 0)
                    or getattr(result, "tokensFreed", 0),
                    getattr(result, "boundary_message", None)
                    or getattr(result, "boundaryMessage", None),
                )
    except (ImportError, AttributeError, Exception):
        pass
    return messages_for_query, 0, None


def _maybe_apply_tool_result_budget(
    messages_for_query: list[Message],
    tool_use_context: ToolUseContext,
    query_source: str,
) -> list[Message]:
    """Enforce per-message budget on aggregate tool result size.

    Mirrors TS L365-394: applies content replacement before microcompact.
    No-ops when contentReplacementState is undefined (feature off).
    """
    if not feature("TOOL_RESULT_BUDGET"):
        return messages_for_query
    try:
        import importlib

        storage = importlib.import_module("hare.utils.tool_result_storage")
        if hasattr(storage, "apply_tool_result_budget"):
            persist = query_source.startswith("agent:") or query_source.startswith(
                "repl_main_thread"
            )
            return storage.apply_tool_result_budget(
                messages_for_query,
                tool_use_context.content_replacement_state
                if hasattr(tool_use_context, "content_replacement_state")
                else None,
                persist,
            )
    except (ImportError, AttributeError, Exception):
        pass
    return messages_for_query


def _maybe_generate_task_summary(
    messages_for_query: list[Message],
    assistant_messages: list[AssistantMessage],
    tool_results: list[Message],
    params: QueryParams,
    tool_use_context: ToolUseContext,
) -> None:
    """Fire mid-turn task summary for 'hare ps' (TS L1681-1702).

    Gated on !agentId (main thread only) and BG_SESSIONS feature.
    """
    if not feature("BG_SESSIONS"):
        return
    if tool_use_context.agent_id:
        return
    try:
        import importlib

        tasks = importlib.import_module("hare.utils.task_summary")
        if hasattr(tasks, "maybe_generate_task_summary"):
            tasks.maybe_generate_task_summary(
                {
                    "systemPrompt": params.system_prompt,
                    "userContext": params.user_context,
                    "systemContext": params.system_context,
                    "toolUseContext": tool_use_context,
                    "forkContextMessages": [
                        *messages_for_query,
                        *assistant_messages,
                        *tool_results,
                    ],
                }
            )
    except (ImportError, AttributeError, Exception):
        return


async def _maybe_apply_context_collapse(
    messages_for_query: list[Message],
    tool_use_context: ToolUseContext,
    query_source: str,
) -> list[Message]:
    """Project collapsed context view before autocompact (TS L428-447).

    Nothing is yielded — the collapsed view is a read-time projection.
    """
    if not feature("CONTEXT_COLLAPSE"):
        return messages_for_query
    try:
        import importlib

        collapse = importlib.import_module("hare.services.context_collapse")
        if hasattr(collapse, "apply_collapses_if_needed"):
            result = collapse.apply_collapses_if_needed(
                messages_for_query,
                tool_use_context,
                query_source,
            )
            if isinstance(result, dict):
                return result.get("messages", messages_for_query)
            if hasattr(result, "messages"):
                return getattr(result, "messages", messages_for_query)
    except (ImportError, AttributeError, Exception):
        pass
    return messages_for_query


# TS SLEEP_TOOL_NAME — used for sleepRan-based command priority (TS L1566)
SLEEP_TOOL_NAME = "Sleep"


def _create_dump_prompts_fetch(
    tool_use_context: ToolUseContext,
    config: Any,
) -> Any:
    """Create dump-prompts fetch wrapper for ant-only debugging (TS L582-590)."""
    try:
        import importlib

        dumps = importlib.import_module("hare.services.api.dump_prompts")
        if hasattr(dumps, "create_dump_prompts_fetch"):
            agent_id = (
                tool_use_context.agent_id
                if tool_use_context.agent_id
                else config.session_id
            )
            return dumps.create_dump_prompts_fetch(agent_id)
    except (ImportError, AttributeError, Exception):
        pass
    return None
