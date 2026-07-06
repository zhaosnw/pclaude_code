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
    """Effective window minus auto-compact buffer."""
    return max(0, get_effective_context_window_size(model) - AUTOCOMPACT_BUFFER_TOKENS)


def _token_count_with_estimation(messages: list[Any]) -> int:
    """Estimate token count from messages."""
    raw = ""
    for m in messages:
        if isinstance(m, dict):
            content = m.get("message", {}).get("content", "")
            raw += content if isinstance(content, str) else str(content)
        elif hasattr(m, "message"):
            msg = getattr(m, "message", None)
            if msg is not None:
                content = getattr(msg, "content", "")
                raw += content if isinstance(content, str) else str(content)
    return estimate_tokens(raw)


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
) -> dict[str, Any]:
    """Run auto-compaction if needed, returning result dict for the query loop.

    Returns a dict with:
        - compactionResult: CompactionResult-like dict or None
        - consecutiveFailures: int or None
    """
    if not is_auto_compact_enabled():
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

    try:
        result: CompactionResult = await compact_conversation(
            messages,
            context=tool_use_context,
            cache_params=cache_safe_params,
            is_auto=True,
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
