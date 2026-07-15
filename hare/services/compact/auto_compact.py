"""
Automatic compaction when context window is tight.

Port of: src/services/compact/autoCompact.ts — orchestration for auto-compaction
decisions and execution within the query loop.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from hare.services.api.claude import get_max_output_tokens_for_model
from hare.services.compact.compact_full import CompactionResult, compact_conversation
from hare.services.token_estimation import estimate_tokens
from hare.services.analytics import log_event

MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
AUTOCOMPACT_BUFFER_TOKENS = 13_000
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3


def get_effective_context_window_size(model: str) -> int:
    """Effective context window minus reserved summary output.

    Uses the model-aware context window (TS getEffectiveContextWindowSize) so
    that models with larger windows (e.g. 1M for Opus 4.8) aren't prematurely
    compacted at 200k. Previously hardcoded 200_000 regardless of model."""
    from hare.utils.context import get_context_window_for_model

    window = get_context_window_for_model(model)
    auto_env = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if auto_env:
        try:
            parsed = int(auto_env, 10)
            if parsed > 0:
                window = min(window, parsed)
        except ValueError:
            pass
    reserved = min(
        get_max_output_tokens_for_model(model), MAX_OUTPUT_TOKENS_FOR_SUMMARY
    )
    return max(0, window - reserved)


def get_auto_compact_threshold(model: str) -> int:
    """Effective window minus auto-compact buffer.

    CLAUDE_AUTOCOMPACT_PCT_OVERRIDE lowers the threshold to a percentage of the
    window (autoCompact.ts:79). The reference exposes it to make auto-compact
    reachable in tests; without it a differential case would have to build a
    real 150k-token conversation.
    """
    window = get_effective_context_window_size(model)
    threshold = max(0, window - AUTOCOMPACT_BUFFER_TOKENS)

    env_percent = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")
    if env_percent:
        try:
            parsed = float(env_percent)
        except ValueError:
            return threshold
        if 0 < parsed <= 100:
            return min(int(window * (parsed / 100)), threshold)
    return threshold


def _message_usage(message: Any) -> Any:
    inner = (
        message.get("message")
        if isinstance(message, dict)
        else getattr(message, "message", None)
    )
    if inner is None:
        return None
    return (
        inner.get("usage") if isinstance(inner, dict) else getattr(inner, "usage", None)
    )


def _tokens_from_usage(usage: Any) -> int:
    def field(name: str) -> int:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
        return int(value or 0)

    return (
        field("input_tokens")
        + field("cache_creation_input_tokens")
        + field("cache_read_input_tokens")
        + field("output_tokens")
    )


def _rough_estimate(messages: list[Any]) -> int:
    raw = ""
    for m in messages:
        inner = m.get("message") if isinstance(m, dict) else getattr(m, "message", None)
        if inner is None:
            continue
        content = (
            inner.get("content", "")
            if isinstance(inner, dict)
            else getattr(inner, "content", "")
        )
        raw += content if isinstance(content, str) else str(content)
    return estimate_tokens(raw)


def _token_count_with_estimation(messages: list[Any]) -> int:
    """Context size: real usage from the last assistant message, plus an
    estimate of everything after it.

    Port of tokenCountWithEstimation (utils/tokens.ts:226). hare estimated from
    message text alone and ignored usage entirely, so a conversation the API
    reports as 150k tokens looked tiny and auto-compact never fired.
    """
    for i in range(len(messages) - 1, -1, -1):
        usage = _message_usage(messages[i])
        if usage:
            return _tokens_from_usage(usage) + _rough_estimate(messages[i + 1 :])
    return _rough_estimate(messages)


def _env_truthy(name: str) -> bool:
    """Mirror TS isEnvTruthy: only '1','true','yes','on' count as enabled.
    A bare os.environ.get truthiness treats 'false'/'0'/'no'/'off' as true,
    diverging from the TS reference."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_auto_compact_enabled() -> bool:
    """Check if automatic compaction is enabled via env and user config.
    Matches TS isAutoCompactEnabled: env vars first, then userConfig.autoCompactEnabled."""
    if _env_truthy("DISABLE_COMPACT") or _env_truthy("DISABLE_AUTO_COMPACT"):
        return False
    try:
        from hare.utils.config import get_global_config

        return get_global_config().auto_compact_enabled
    except Exception:
        return True


def calculate_token_warning_state(
    token_usage: int,
    model: str,
) -> dict[str, Any]:
    """Calculate warning/error/blocking thresholds for token usage.
    Mirrors TS calculateTokenWarningState (autoCompact.ts:93-145):
    >= operators, threshold gating on isAutoCompactEnabled, model-aware
    blocking limit."""
    effective_window = get_effective_context_window_size(model)
    if effective_window <= 0:
        return {
            "percentLeft": 0,
            "isAboveWarningThreshold": False,
            "isAboveErrorThreshold": False,
            "isAboveAutoCompactThreshold": False,
            "isAtBlockingLimit": False,
        }
    # TS uses the auto-compact threshold as the basis for percentLeft
    threshold = get_auto_compact_threshold(
        model
    ) if is_auto_compact_enabled() else effective_window
    percent_left = max(
        0, round((threshold - token_usage) / threshold * 100)
    ) if threshold > 0 else 0
    return {
        "percentLeft": percent_left,
        "isAboveWarningThreshold": token_usage
        >= (effective_window - WARNING_THRESHOLD_BUFFER_TOKENS),
        "isAboveErrorThreshold": token_usage
        >= (effective_window - ERROR_THRESHOLD_BUFFER_TOKENS),
        # TS gates this on isAutoCompactEnabled() — when disabled, always false
        "isAboveAutoCompactThreshold": (
            is_auto_compact_enabled()
            and token_usage >= get_auto_compact_threshold(model)
        ),
        "isAtBlockingLimit": token_usage
        >= (effective_window - MANUAL_COMPACT_BUFFER_TOKENS),
    }


def should_auto_compact(
    messages: list[Any],
    model: str,
    query_source: Optional[str] = None,
    snip_tokens_freed: int = 0,
) -> bool:
    """Determine if auto-compaction should run for these messages."""
    # Never auto-compact for compaction or session_memory agents
    if query_source in ("compact", "session_memory"):
        return False
    # Check if auto-compact is enabled
    if not is_auto_compact_enabled():
        return False
    # Count tokens
    token_count = _token_count_with_estimation(messages) - snip_tokens_freed
    return token_count >= get_auto_compact_threshold(model)


async def auto_compact_if_needed(
    messages: list[Any],
    tool_use_context: Any,
    cache_safe_params: dict[str, Any],
    query_source: Optional[str] = None,
    tracking: Optional[dict[str, Any]] = None,
    snip_tokens_freed: int = 0,
    call_model: Any = None,
) -> dict[str, Any]:
    """Run auto-compaction if needed, returning result dict for the query loop.

    ``call_model`` is the query loop's model callable: the reference generates
    the summary with a real model turn, so compaction must go through the same
    path (and, under a fixture, consume the same response).

    Returns a dict with:
        - compactionResult: CompactionResult-like dict or None
        - consecutiveFailures: int or None
    """
    if not is_auto_compact_enabled():
        return {"compactionResult": None, "consecutiveFailures": None}

    # The released CLI does not auto-compact a headless print run. Verified
    # against 2.1.209: a genuinely 300k-token conversation, with
    # CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=1, still issues no compaction request.
    # `-p` is single-shot; compaction is for long-lived REPL/resume sessions.
    options = getattr(tool_use_context, "options", None)
    if getattr(options, "is_non_interactive_session", False):
        return {"compactionResult": None, "consecutiveFailures": None}

    # Circuit breaker: stop retrying after consecutive failures
    if (
        tracking
        and tracking.get("consecutiveFailures", 0)
        >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
    ):
        return {
            "compactionResult": None,
            "consecutiveFailures": tracking["consecutiveFailures"],
        }

    model = _get_model_from_context(tool_use_context)

    if not should_auto_compact(messages, model, query_source, snip_tokens_freed):
        return {"compactionResult": None, "consecutiveFailures": None}

    from hare.utils.hooks import (
        execute_post_compact_hooks,
        execute_pre_compact_hooks,
        execute_stop_hooks,
    )

    try:
        # The reference brackets compaction with PreCompact/PostCompact
        # (hooks.ts:3974,4046); hare fired neither.
        await execute_pre_compact_hooks(
            trigger="auto", tool_use_context=tool_use_context
        )

        result: CompactionResult = await compact_conversation(
            messages,
            context=tool_use_context,
            cache_params=cache_safe_params,
            is_auto=True,
            call_model=call_model,
        )

        # The reference runs the summarization as a subagent query, so a
        # SubagentStop hook fires for it even when the turn dispatched no Task
        # (confirmed by probing the reference with a compact-only fixture).
        # hare summarizes with a direct model call, so emit it explicitly.
        async for _ in execute_stop_hooks(
            agent_id="compact",
            tool_use_context=tool_use_context,
            messages=list(messages),
            agent_type="compact",
        ):
            pass

        await execute_post_compact_hooks(
            trigger="auto",
            compact_summary=result.summary,
            tool_use_context=tool_use_context,
        )

        # Build compactionResult in the shape core.py expects.
        # The `messages` key is deliberately OMITTED here — TS CompactionResult
        # has no `messages` key (only summaryMessages+attachments+hookResults);
        # previously setting both summaryMessages AND messages to new_messages
        # (the same list!) caused _build_post_compact_messages to iterate both
        # keys and double-append every post-compact message.
        compaction_dict = {
            "summaryMessages": result.new_messages,
            "attachments": [],
            "hookResults": [],
            "preCompactTokenCount": result.tokens_before,
            "postCompactTokenCount": result.tokens_after,
            "truePostCompactTokenCount": result.tokens_after,
            "compactionUsage": None,
        }

        log_event(
            "tengu_auto_compact_succeeded",
            {
                "originalMessageCount": len(messages),
                "compactedMessageCount": len(result.new_messages),
                "preCompactTokenCount": result.tokens_before,
                "postCompactTokenCount": result.tokens_after,
                "truePostCompactTokenCount": result.tokens_after,
            },
        )

        return {"compactionResult": compaction_dict, "consecutiveFailures": 0}

    except Exception as e:
        consecutive = tracking.get("consecutiveFailures", 0) + 1 if tracking else 1
        log_event(
            "tengu_auto_compact_failed",
            {
                "error": str(e),
                "consecutiveFailures": consecutive,
            },
        )
        return {"compactionResult": None, "consecutiveFailures": consecutive}


def _get_model_from_context(tool_use_context: Any) -> str:
    """Extract model name from tool_use_context."""
    if hasattr(tool_use_context, "options"):
        opts = tool_use_context.options
        if hasattr(opts, "main_loop_model") and opts.main_loop_model:
            return opts.main_loop_model
    return os.environ.get("CLAUDE_CODE_MODEL", "claude-sonnet-4-6-20260301")


async def maybe_run_auto_compact(
    messages: list[dict[str, Any]],
    model: str,
) -> CompactionResult | None:
    """Run compaction if over threshold (simple entry point)."""
    del model
    if len(messages) < 2:
        return None
    return await compact_conversation(messages)
