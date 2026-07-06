"""
Micro-compact - lightweight compaction of tool results with caching support.

Port of: src/services/compact/microCompact.ts

Reduces token usage by:
  1. Truncating large tool results in older messages (basic path).
  2. Using Claude API to intelligently summarize large contexts (cached path).
  3. Injecting cache control breakpoints so subsequent API calls benefit
     from prompt caching of the compacted context.
  4. Emitting pendingCacheEdits so the query loop can signal cache deletions
     to the API via microcompact_boundary messages.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from hare.services.token_estimation import estimate_tokens
from hare.services.analytics import log_event
from hare.utils.bundle_feature import feature

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"
IMAGE_MAX_TOKEN_SIZE = 2000

# Tools whose results are eligible for micro-compaction.
COMPACTABLE_TOOLS = frozenset(
    {
        "FileRead",
        "Bash",
        "PowerShell",
        "Grep",
        "Glob",
        "WebSearch",
        "WebFetch",
        "FileEdit",
        "FileWrite",
    }
)

# When tokens saved exceed this, we emit a pendingCacheEdits signal.
CACHE_EDITS_MIN_TOKEN_THRESHOLD = 500

# Maximum number of messages to keep un-compacted at the tail.
DEFAULT_KEEP_RECENT = 4

# Default token threshold for API-based summarization.
DEFAULT_API_SUMMARIZE_THRESHOLD = 2000

# Maximum tokens for the micro-compact model call response.
MICROCOMPACT_MAX_OUTPUT_TOKENS = 1024

# Default gap threshold for time-based microcompact.
DEFAULT_TIME_GAP_THRESHOLD_SECONDS = 60 * 60  # 1 hour


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _env_truthy(name: str) -> bool:
    """Check if an env var is truthy (1, true, yes, on)."""
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def _parse_env_int(name: str, default: int) -> int:
    """Parse an integer env var, returning *default* on failure."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw, 10)
    except (ValueError, TypeError):
        return default


def _describe_compactable_by_tool(
    compactable: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build a per-tool summary dict for compactable items."""
    by_tool: dict[str, dict[str, Any]] = {}
    for item in compactable:
        tool = item.get("tool_name", "unknown") or "unknown"
        if tool not in by_tool:
            by_tool[tool] = {"count": 0, "total_tokens": 0, "items": []}
        by_tool[tool]["count"] += 1
        by_tool[tool]["total_tokens"] += item.get("token_count", 0)
        if len(by_tool[tool]["items"]) < 5:
            by_tool[tool]["items"].append(
                {
                    "message_index": item["message_index"],
                    "token_count": item["token_count"],
                    "tool_use_id": item.get("tool_use_id", ""),
                }
            )
    return by_tool


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MicroCompactResult:
    """Result of a micro-compaction pass."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    tokens_saved: int = 0
    compaction_info: Optional[CompactionInfo] = None


@dataclass
class CompactionInfo:
    """Metadata about the micro-compaction that was performed.

    Used by query/core.py to reason about cache invalidation and
    to emit microcompact_boundary messages.
    """

    trigger: str = "auto"
    deleted_tool_ids: list[str] = field(default_factory=list)
    baseline_cache_deleted_tokens: int = 0
    pending_cache_edits: Optional[PendingCacheEdits] = None


@dataclass
class PendingCacheEdits:
    """Hints for the query loop to signal cache deletions to the API."""

    trigger: str = "auto"
    deleted_tool_ids: list[str] = field(default_factory=list)
    baseline_cache_deleted_tokens: int = 0


@dataclass
class TimeGapInfo:
    """Information about a time gap between messages."""

    gap_seconds: float = 0.0
    index_before_gap: int = 0
    token_count_before_gap: int = 0


@dataclass
class MicroCompactConfig:
    """Central configuration for all micro-compact strategies.

    Attributes:
        enabled: Master switch for all micro-compaction.
        api_summarize_enabled: Use API-based summarization path.
        time_based_enabled: Use time-gap-based clearing.
        cache_edits_enabled: Emit cache_edits signals for the query loop.
        threshold_tokens: Minimum block token count to consider for compaction.
        api_summarize_threshold: Minimum total tokens to trigger API summarization.
        keep_recent: Number of most-recent messages to preserve unmodified.
        max_summarize_items: Cap on items sent to the API summarizer.
        summarization_model: Override model for microcompact API calls.
        api_timeout_seconds: Timeout for the summarization API call.
        gap_threshold_seconds: Minimum time gap for time-based clearing.
        min_tokens_for_cache_edits: Tokens-saved floor for emitting cache_edits.
    """

    enabled: bool = True
    api_summarize_enabled: bool = False
    time_based_enabled: bool = False
    cache_edits_enabled: bool = False
    threshold_tokens: int = 1000
    api_summarize_threshold: int = 2000
    keep_recent: int = 4
    max_summarize_items: int = 50
    summarization_model: str = ""
    api_timeout_seconds: float = 15.0
    gap_threshold_seconds: float = float(DEFAULT_TIME_GAP_THRESHOLD_SECONDS)
    min_tokens_for_cache_edits: int = CACHE_EDITS_MIN_TOKEN_THRESHOLD


def get_microcompact_config() -> MicroCompactConfig:
    """Build a MicroCompactConfig from environment variables and feature flags.

    Reads env vars for fine-tuning; falls back to sensible defaults.
    """
    return MicroCompactConfig(
        enabled=not _env_truthy("DISABLE_MICROCOMPACT"),
        api_summarize_enabled=feature("CACHED_MICROCOMPACT"),
        time_based_enabled=feature("TIME_BASED_MICROCOMPACT"),
        cache_edits_enabled=feature("CACHED_MICROCOMPACT"),
        threshold_tokens=_parse_env_int(
            "MICROCOMPACT_THRESHOLD_TOKENS", 1000
        ),
        api_summarize_threshold=_parse_env_int(
            "MICROCOMPACT_API_SUMMARIZE_THRESHOLD", 2000
        ),
        keep_recent=_parse_env_int(
            "MICROCOMPACT_KEEP_RECENT", DEFAULT_KEEP_RECENT
        ),
        max_summarize_items=_parse_env_int(
            "MICROCOMPACT_MAX_SUMMARIZE_ITEMS", 50
        ),
        summarization_model=os.environ.get("CLAUDE_CODE_MICROCOMPACT_MODEL", ""),
        api_timeout_seconds=float(
            _parse_env_int("MICROCOMPACT_API_TIMEOUT", 15)
        ),
        gap_threshold_seconds=float(
            _parse_env_int(
                "MICROCOMPACT_GAP_THRESHOLD_SECONDS",
                DEFAULT_TIME_GAP_THRESHOLD_SECONDS,
            )
        ),
        min_tokens_for_cache_edits=_parse_env_int(
            "MICROCOMPACT_CACHE_EDITS_MIN_TOKENS",
            CACHE_EDITS_MIN_TOKEN_THRESHOLD,
        ),
    )


def should_microcompact(
    messages: list[dict[str, Any]],
    *,
    config: Optional[MicroCompactConfig] = None,
    threshold_tokens: int = 0,
) -> bool:
    """Gate check — returns True when micro-compaction should be applied.

    Evaluates:
      - Config must be enabled.
      - There must be enough messages (more than keep_recent).
      - At least one compactable block must exceed the token threshold.
    """
    cfg = config or get_microcompact_config()
    if not cfg.enabled:
        return False

    if len(messages) <= cfg.keep_recent:
        return False

    effective_threshold = threshold_tokens or cfg.threshold_tokens

    try:
        compactable = _identify_compactable_blocks(messages, effective_threshold)
    except Exception:
        return False

    return len(compactable) > 0


def estimate_compact_savings(
    messages: list[dict[str, Any]],
    *,
    threshold_tokens: int = 1000,
) -> dict[str, Any]:
    """Estimate token savings from micro-compaction without modifying messages.

    Returns a dict with:
        - total_compactable_items: count of blocks eligible for compaction
        - total_token_count: sum of tokens in compactable content
        - estimated_savings_truncation: savings from truncation path
        - estimated_savings_api: savings from API summarization path
        - estimated_savings_time_based: savings from time-based path
        - breakdown_by_tool: per-tool-type savings estimates
    """
    try:
        compactable = _identify_compactable_blocks(messages, threshold_tokens)
    except Exception:
        return {
            "total_compactable_items": 0,
            "total_token_count": 0,
            "estimated_savings_truncation": 0,
            "estimated_savings_api": 0,
            "estimated_savings_time_based": 0,
            "breakdown_by_tool": {},
        }

    total_token_count = sum(item["token_count"] for item in compactable)
    truncation_savings = sum(
        item["tokens_after_truncation"] for item in compactable
    )
    api_savings = total_token_count - (len(compactable) * 200)  # rough: 200-token summaries
    time_based_savings = sum(
        item["tokens_after_truncation"]
        for item in compactable
        if item.get("is_older")
    )

    breakdown: dict[str, int] = {}
    for item in compactable:
        tool = item.get("tool_name", "unknown") or "unknown"
        breakdown[tool] = breakdown.get(tool, 0) + item["token_count"]

    return {
        "total_compactable_items": len(compactable),
        "total_token_count": max(0, total_token_count),
        "estimated_savings_truncation": max(0, truncation_savings),
        "estimated_savings_api": max(0, api_savings),
        "estimated_savings_time_based": max(0, time_based_savings),
        "breakdown_by_tool": breakdown,
    }


def dry_run_microcompact(
    messages: list[dict[str, Any]],
    *,
    config: Optional[MicroCompactConfig] = None,
    threshold_tokens: int = 1000,
) -> dict[str, Any]:
    """Preview what micro-compaction would do without modifying messages.

    Returns a dict with pre/post token counts, which items would be
    compacted, and which path would be chosen.
    """
    cfg = config or get_microcompact_config()
    pre_tokens = estimate_message_tokens(messages)

    try:
        compactable = _identify_compactable_blocks(messages, threshold_tokens)
    except Exception:
        compactable = []

    if not compactable:
        return {
            "would_compact": False,
            "pre_tokens": pre_tokens,
            "post_tokens": pre_tokens,
            "tokens_saved": 0,
            "path": "none",
            "items_compacted": 0,
            "compactable_items": [],
        }

    older_items = [item for item in compactable if item.get("is_older")]
    total_compactable_tokens = sum(item["token_count"] for item in older_items)

    path = "truncation"
    if cfg.api_summarize_enabled and total_compactable_tokens >= cfg.api_summarize_threshold:
        path = "api_summarize"
    elif cfg.time_based_enabled:
        path = "time_based"

    truncation_savings = sum(
        item["tokens_after_truncation"] for item in compactable
    )

    # Build previewable item summaries
    compactable_items: list[dict[str, Any]] = []
    for item in compactable[:20]:  # limit preview count
        compactable_items.append(
            {
                "message_index": item["message_index"],
                "tool_name": item.get("tool_name", "unknown"),
                "tool_use_id": item.get("tool_use_id", ""),
                "token_count": item["token_count"],
                "is_older": item.get("is_older", False),
                "content_preview": (
                    item["content_text"][:100]
                    if isinstance(item.get("content_text"), str)
                    else ""
                ),
            }
        )

    return {
        "would_compact": True,
        "pre_tokens": pre_tokens,
        "post_tokens": max(0, pre_tokens - truncation_savings),
        "tokens_saved": truncation_savings,
        "path": path,
        "items_compacted": len(compactable),
        "compactable_items": compactable_items,
        "compactable_by_tool": _describe_compactable_by_tool(compactable),
    }


# ---------------------------------------------------------------------------
# Basic microcompact (existing path — token-based truncation)
# ---------------------------------------------------------------------------


async def microcompact_messages(
    messages: list[dict[str, Any]],
    context: Any = None,
    query_source: str = "",
    *,
    threshold_tokens: int = 1000,
) -> dict[str, Any]:
    """Apply micro-compaction to messages by truncating large tool results.

    When CACHED_MICROCOMPACT feature is active, delegates to the
    cache-aware API path. Otherwise falls back to simple truncation.

    Returns dict with:
        messages: list of compacted messages
        tokens_saved: estimated token reduction
        compaction_info / compactionInfo: metadata for cache_edits
    """
    if not messages:
        return {"messages": [], "tokens_saved": 0}

    try:
        if feature("CACHED_MICROCOMPACT"):
            return await _cached_microcompact(
                messages, context, query_source, threshold_tokens
            )
        return await _basic_microcompact(messages, threshold_tokens)
    except Exception as exc:
        log_event(
            "tengu_microcompact_error",
            {
                "error": str(exc),
                "message_count": len(messages),
                "query_source": query_source,
            },
        )
        # Return original messages untouched on any unexpected error
        return {"messages": list(messages), "tokens_saved": 0}


async def _basic_microcompact(
    messages: list[dict[str, Any]],
    threshold_tokens: int = 1000,
) -> dict[str, Any]:
    """Simple token-based truncation (original path)."""
    if not messages:
        return {"messages": [], "tokens_saved": 0}

    new_messages: list[dict[str, Any]] = []
    tokens_saved = 0
    deleted_tool_ids: list[str] = []

    for i, msg in enumerate(messages):
        # Only compact older messages (keep last few intact)
        if i >= len(messages) - DEFAULT_KEEP_RECENT:
            new_messages.append(msg)
            continue

        msg_type = msg.get("type", "")
        if msg_type not in ("user", "assistant"):
            new_messages.append(msg)
            continue

        # Guard: ensure message sub-dict exists
        inner = msg.get("message")
        if not isinstance(inner, dict):
            new_messages.append(msg)
            continue

        content = inner.get("content", [])
        if not isinstance(content, list):
            new_messages.append(msg)
            continue

        new_content: list[dict[str, Any]] = []
        modified = False
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type", "")
            if block_type != "tool_result":
                new_content.append(block)
                continue

            result_content = block.get("content", "")
            if not isinstance(result_content, str):
                new_content.append(block)
                continue

            # Skip already-compacted blocks (idempotency)
            if block.get("is_compacted"):
                new_content.append(block)
                continue

            # Skip empty content
            if not result_content.strip():
                new_content.append(block)
                continue

            token_count = estimate_tokens(result_content)
            if token_count <= threshold_tokens:
                new_content.append(block)
                continue

            truncated = (
                result_content[:500]
                + f"\n\n[... truncated {token_count} tokens ...]"
            )
            new_content.append({**block, "content": truncated, "is_compacted": True})
            tokens_saved += max(0, token_count - estimate_tokens(truncated))
            modified = True

            tool_use_id = block.get("tool_use_id", "")
            if tool_use_id:
                deleted_tool_ids.append(str(tool_use_id))

        if modified:
            new_msg = {**msg, "message": {**inner, "content": new_content}}
            new_messages.append(new_msg)
        else:
            new_messages.append(msg)

    result: dict[str, Any] = {
        "messages": new_messages,
        "tokens_saved": tokens_saved,
    }

    if feature("CACHED_MICROCOMPACT") and tokens_saved >= CACHE_EDITS_MIN_TOKEN_THRESHOLD:
        result["compaction_info"] = {
            "trigger": "auto",
            "deleted_tool_ids": deleted_tool_ids,
            "baseline_cache_deleted_tokens": tokens_saved,
            "pending_cache_edits": {
                "trigger": "auto",
                "deletedToolIds": deleted_tool_ids,
                "baselineCacheDeletedTokens": tokens_saved,
            },
        }

    return result


# ---------------------------------------------------------------------------
# Cached microcompact (API-based, with cache_edits)
# ---------------------------------------------------------------------------


async def _cached_microcompact(
    messages: list[dict[str, Any]],
    context: Any,
    query_source: str,
    threshold_tokens: int = 1000,
) -> dict[str, Any]:
    """Cache-aware microcompact using Claude API summarization.

    Pipeline:
      1. Identify compactable blocks in older messages.
      2. Attempt API-based summarization for large groups.
      3. Apply cache control breakpoints to preserved messages.
      4. Emit compactionInfo with pendingCacheEdits for the query loop.
    """
    if not messages or len(messages) <= DEFAULT_KEEP_RECENT:
        return {"messages": list(messages), "tokens_saved": 0}

    # Identify compactable tool results — guard against malformed messages
    try:
        compactable = _identify_compactable_blocks(messages, threshold_tokens)
    except Exception as exc:
        log_event(
            "tengu_microcompact_identify_error",
            {"error": str(exc), "message_count": len(messages)},
        )
        return {"messages": list(messages), "tokens_saved": 0}

    if not compactable:
        return {"messages": list(messages), "tokens_saved": 0}

    # Compute token savings from truncation (always available baseline)
    truncation_tokens_saved = sum(
        item["tokens_after_truncation"] for item in compactable
    )

    # Try time-based microcompact first
    try:
        time_gap_result = await _try_time_based_microcompact(
            messages, context, compactable
        )
    except Exception as exc:
        log_event(
            "tengu_microcompact_time_based_error",
            {"error": str(exc)},
        )
        time_gap_result = None

    if time_gap_result is not None:
        msgs = time_gap_result.get("messages")
        if msgs is not None and isinstance(msgs, list):
            return time_gap_result

    # Try API-based summarization for large groups
    try:
        api_result = await _try_api_microcompact_summarize(
            messages, context, compactable, query_source
        )
    except Exception as exc:
        log_event(
            "tengu_microcompact_api_error",
            {"error": str(exc), "query_source": query_source},
        )
        api_result = None

    if api_result is not None:
        msgs = api_result.get("messages")
        if msgs is not None and isinstance(msgs, list):
            return api_result

    # Fallback: apply truncation with cache_edits
    try:
        return _apply_truncation_with_cache_edits(
            messages, compactable, truncation_tokens_saved
        )
    except Exception as exc:
        log_event(
            "tengu_microcompact_truncation_error",
            {"error": str(exc)},
        )
        return {"messages": list(messages), "tokens_saved": 0}


# ---------------------------------------------------------------------------
# Compactable block identification
# ---------------------------------------------------------------------------


def _identify_compactable_blocks(
    messages: list[dict[str, Any]],
    threshold_tokens: int,
) -> list[dict[str, Any]]:
    """Walk messages and collect compactable tool result blocks.

    Returns list of dicts with:
        message_index, block_index, block, tool_name, tool_use_id,
        content_text, token_count, tokens_after_truncation, is_older
    """
    compactable: list[dict[str, Any]] = []
    keep_recent = DEFAULT_KEEP_RECENT

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue

        is_older = i < len(messages) - keep_recent
        msg_type = msg.get("type", "")

        # Only user messages carry tool_result blocks
        if msg_type != "user":
            continue

        inner = msg.get("message")
        if not isinstance(inner, dict):
            continue

        content = inner.get("content", [])
        if not isinstance(content, list):
            continue

        for j, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue

            # Skip already-compacted blocks (idempotency)
            if block.get("is_compacted"):
                continue

            tool_name = _infer_tool_name(block)
            # Tool names are often not present on tool_result blocks.
            # Compact unknown tools by default; only skip known
            # non-compactable tools (e.g. structured-output tools).
            if tool_name and tool_name not in COMPACTABLE_TOOLS:
                continue

            result_content = block.get("content", "")
            if not isinstance(result_content, str):
                continue

            # Skip empty or whitespace-only content
            if not result_content.strip():
                continue

            token_count = estimate_tokens(result_content)
            if token_count <= threshold_tokens:
                continue

            # Compute token savings from truncating
            truncated = (
                result_content[:500]
                + f"\n\n[... truncated {token_count} tokens ...]"
            )
            tokens_saved = max(0, token_count - estimate_tokens(truncated))

            compactable.append(
                {
                    "message_index": i,
                    "block_index": j,
                    "block": block,
                    "tool_name": tool_name,
                    "tool_use_id": str(block.get("tool_use_id", "")),
                    "content_text": result_content,
                    "token_count": token_count,
                    "tokens_after_truncation": tokens_saved,
                    "is_older": is_older,
                }
            )

    return compactable


def _infer_tool_name(block: dict[str, Any]) -> str:
    """Try to infer the tool name from a tool_result block."""
    # tool_result blocks don't carry the tool name directly.
    # We look for tool_name metadata or infer from context.
    tool_name = block.get("tool_name", "")
    if not tool_name:
        tool_name = block.get("toolName", "")
    if not tool_name:
        # Look up from the block's tool_use_id in metadata
        tool_meta = block.get("_tool_meta", {})
        tool_name = tool_meta.get("name", "")
    return str(tool_name)


def compactable_tool_blocks(
    messages: list[dict[str, Any]],
    *,
    threshold_tokens: int = 1000,
    compactable_tools: Optional[frozenset[str]] = None,
) -> list[dict[str, Any]]:
    """Extract compactable tool result blocks from messages.

    A convenience wrapper around _identify_compactable_blocks that applies
    an optional tool-name filter. Returns blocks that match both the token
    threshold and (if provided) the allowed-tools set.
    """
    items = _identify_compactable_blocks(messages, threshold_tokens)
    if compactable_tools is None:
        return items
    # Filter to only the requested tools
    return [
        item for item in items
        if (item.get("tool_name") or "") in compactable_tools
    ]


def tokens_to_compact(
    messages: list[dict[str, Any]],
    *,
    threshold_tokens: int = 1000,
    include_recent: bool = False,
) -> int:
    """Count the total tokens in compactable blocks across all messages.

    By default *excludes* the keep_recent tail. Set include_recent=True
    to count everything.
    """
    try:
        blocks = _identify_compactable_blocks(messages, threshold_tokens)
    except Exception:
        return 0

    if not include_recent:
        blocks = [b for b in blocks if b.get("is_older")]

    return sum(b["token_count"] for b in blocks)


# ---------------------------------------------------------------------------
# Selective retention microcompact
# ---------------------------------------------------------------------------


# Tool names whose results should always be retained (never cleared).
# These produce stateful side-effects: losing their outputs would degrade
# the model's understanding of the current workspace state.
PRESERVE_TOOLS: frozenset[str] = frozenset(
    {
        "TodoWrite",
        "TaskCreate",
        "TaskUpdate",
        "NotebookEdit",
        "EnterWorktree",
        "ExitWorktree",
        "Skill",
        "Task",
    }
)


async def selective_retention_microcompact(
    messages: list[dict[str, Any]],
    *,
    threshold_tokens: int = 1000,
    preserve_tools: Optional[frozenset[str]] = None,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> dict[str, Any]:
    """Micro-compact with selective retention of important tool results.

    Unlike full compaction, this preserves results from critical tools
    (TodoWrite, TaskCreate, etc.) while still compacting large, ephemeral
    results (Bash, FileRead, WebSearch, etc.).

    Returns dict with messages, tokens_saved, and per-group metadata.
    """
    preserve = preserve_tools or PRESERVE_TOOLS

    if len(messages) <= keep_recent:
        return {"messages": list(messages), "tokens_saved": 0}

    new_messages: list[dict[str, Any]] = []
    tokens_saved = 0
    preserved_count = 0
    compacted_count = 0
    deleted_tool_ids: list[str] = []

    for i, msg in enumerate(messages):
        is_recent = i >= len(messages) - keep_recent

        if msg.get("type") != "user":
            new_messages.append(msg)
            continue

        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            new_messages.append(msg)
            continue

        new_content: list[dict[str, Any]] = []
        modified = False

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                new_content.append(block)
                continue

            tool_name = _infer_tool_name(block)
            result_content = block.get("content", "")

            # Always preserve results from critical tools
            if tool_name in preserve:
                preserved_count += 1
                new_content.append(block)
                continue

            # Preserve recent results regardless of tool type
            if is_recent:
                new_content.append(block)
                continue

            if not isinstance(result_content, str):
                new_content.append(block)
                continue

            token_count = estimate_tokens(result_content)
            if token_count <= threshold_tokens:
                new_content.append(block)
                continue

            # Compact this block
            truncated = (
                result_content[:500]
                + f"\n\n[... truncated {token_count} tokens ...]"
            )
            new_content.append({**block, "content": truncated, "is_compacted": True})
            tokens_saved += token_count - estimate_tokens(truncated)
            compacted_count += 1
            modified = True

            tool_use_id = block.get("tool_use_id", "")
            if tool_use_id:
                deleted_tool_ids.append(str(tool_use_id))

        if modified:
            new_msg = {**msg, "message": {**msg["message"], "content": new_content}}
            new_messages.append(new_msg)
        else:
            new_messages.append(msg)

    result: dict[str, Any] = {
        "messages": new_messages,
        "tokens_saved": tokens_saved,
        "preserved_count": preserved_count,
        "compacted_count": compacted_count,
    }

    if tokens_saved >= CACHE_EDITS_MIN_TOKEN_THRESHOLD and deleted_tool_ids:
        result["compaction_info"] = {
            "trigger": "selective_retention",
            "deleted_tool_ids": deleted_tool_ids,
            "baseline_cache_deleted_tokens": tokens_saved,
            "pending_cache_edits": {
                "trigger": "selective_retention",
                "deletedToolIds": deleted_tool_ids,
                "baselineCacheDeletedTokens": tokens_saved,
            },
        }

    log_event(
        "tengu_microcompact_selective_retention",
        {
            "tokens_saved": tokens_saved,
            "preserved_count": preserved_count,
            "compacted_count": compacted_count,
        },
    )

    return result


# ---------------------------------------------------------------------------
# Time-based microcompact
# ---------------------------------------------------------------------------


async def _try_time_based_microcompact(
    messages: list[dict[str, Any]],
    context: Any,
    compactable: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Attempt time-based microcompact using message timestamp gaps."""
    try:
        from hare.services.compact.time_based_mc_config import get_time_based_mc_config
    except ImportError:
        return None

    config = get_time_based_mc_config()
    if not config.enabled:
        return None

    gap_threshold_seconds = config.gap_threshold_minutes * 60
    keep_recent = config.keep_recent

    # Find time gaps between messages
    gaps = _find_time_gaps(messages, gap_threshold_seconds)

    if not gaps:
        return None

    # Find compactable items that fall before the first significant gap
    first_gap_idx = gaps[0].index_before_gap
    if first_gap_idx <= keep_recent:
        return None

    # Collect items to compact (before the gap, not in the keep_recent window)
    items_to_compact = [
        item for item in compactable
        if item["message_index"] < first_gap_idx
        and item["message_index"] < len(messages) - keep_recent
    ]
    if not items_to_compact:
        return None

    return _apply_time_based_clearing(
        messages, items_to_compact, first_gap_idx, gaps[0]
    )


def _find_time_gaps(
    messages: list[dict[str, Any]],
    gap_threshold_seconds: float,
) -> list[TimeGapInfo]:
    """Find significant time gaps between consecutive messages."""
    gaps: list[TimeGapInfo] = []
    token_count_so_far = 0

    for i in range(len(messages) - 1):
        token_count_so_far += _estimate_msg_tokens(messages[i])
        ts_current = _extract_timestamp(messages[i])
        ts_next = _extract_timestamp(messages[i + 1])

        if ts_current is None or ts_next is None:
            continue

        gap = ts_next - ts_current
        if gap >= gap_threshold_seconds:
            gaps.append(
                TimeGapInfo(
                    gap_seconds=gap,
                    index_before_gap=i,
                    token_count_before_gap=token_count_so_far,
                )
            )

    return gaps


def _extract_timestamp(msg: dict[str, Any]) -> Optional[float]:
    """Extract a Unix timestamp (seconds) from a message dict."""
    # Check direct timestamp field
    ts = msg.get("timestamp")
    if ts is not None:
        if isinstance(ts, (int, float)):
            # Could be seconds or milliseconds
            if ts > 1_000_000_000_000:
                return ts / 1000.0
            return float(ts)
        if isinstance(ts, str):
            try:
                return float(ts)
            except (ValueError, TypeError):
                pass

    # Check ISO-format timestamp
    iso_ts = msg.get("timestamp_iso", msg.get("created_at"))
    if isinstance(iso_ts, str):
        try:
            # Parse ISO 8601
            import datetime
            dt = datetime.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError, AttributeError):
            pass

    return None


def _apply_time_based_clearing(
    messages: list[dict[str, Any]],
    items_to_compact: list[dict[str, Any]],
    gap_index: int,
    gap_info: TimeGapInfo,
) -> dict[str, Any]:
    """Clear old tool result content that sits before a time gap."""
    del gap_index  # used only by caller for boundary decisions

    new_messages = list(messages)
    deleted_tool_ids: list[str] = []
    total_tokens_saved = 0

    # Group items by message_index for efficient replacement
    items_by_msg: dict[int, list[dict[str, Any]]] = {}
    for item in items_to_compact:
        idx = item.get("message_index", -1)
        if idx < 0 or idx >= len(new_messages):
            continue  # skip invalid indices
        items_by_msg.setdefault(idx, []).append(item)

    for msg_idx, items in items_by_msg.items():
        msg = new_messages[msg_idx]
        inner = msg.get("message")
        if not isinstance(inner, dict):
            continue

        content = list(inner.get("content", []))
        if not isinstance(content, list):
            continue

        modified = False

        for item in items:
            block_idx = item.get("block_index", -1)
            if block_idx < 0 or block_idx >= len(content):
                continue

            block = content[block_idx]
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue

            # Skip if already compacted / cleared (idempotency)
            if block.get("content") == TIME_BASED_MC_CLEARED_MESSAGE:
                continue

            content[block_idx] = {
                **block,
                "content": TIME_BASED_MC_CLEARED_MESSAGE,
                "is_compacted": True,
            }
            total_tokens_saved += item.get("tokens_after_truncation", 0)
            modified = True
            tid = item.get("tool_use_id", "")
            if tid:
                deleted_tool_ids.append(tid)

        if modified:
            new_messages[msg_idx] = {
                **msg,
                "message": {**inner, "content": content},
            }

    result: dict[str, Any] = {
        "messages": new_messages,
        "tokens_saved": total_tokens_saved,
    }

    if (
        feature("CACHED_MICROCOMPACT")
        and total_tokens_saved >= CACHE_EDITS_MIN_TOKEN_THRESHOLD
    ):
        result["compaction_info"] = {
            "trigger": "time_gap",
            "deleted_tool_ids": deleted_tool_ids,
            "baseline_cache_deleted_tokens": total_tokens_saved,
            "pending_cache_edits": {
                "trigger": "time_gap",
                "deletedToolIds": deleted_tool_ids,
                "baselineCacheDeletedTokens": total_tokens_saved,
            },
        }

    log_event(
        "tengu_microcompact_time_based",
        {
            "trigger": "time_gap",
            "tokens_saved": total_tokens_saved,
            "gap_seconds": gap_info.gap_seconds,
            "deleted_tool_count": len(deleted_tool_ids),
        },
    )

    return result


# ---------------------------------------------------------------------------
# API-based microcompact summarization
# ---------------------------------------------------------------------------


async def _try_api_microcompact_summarize(
    messages: list[dict[str, Any]],
    context: Any,
    compactable: list[dict[str, Any]],
    query_source: str,
) -> Optional[dict[str, Any]]:
    """Use the Claude API to summarize large tool results groups.

    Only fires when compactable items collectively exceed the API
    summarize threshold. Uses a small, fast model call to produce
    concise summaries of old tool output.
    """
    # Filter to older messages only
    older_items = [
        item for item in compactable
        if item["is_older"]
    ]
    if not older_items:
        return None

    total_compactable_tokens = sum(item["token_count"] for item in older_items)
    if total_compactable_tokens < DEFAULT_API_SUMMARIZE_THRESHOLD:
        return None

    # Build summarization prompt
    summarize_items = older_items[:50]  # Cap at 50 items to avoid huge prompts
    prompt = _build_summarize_prompt(summarize_items)

    if not prompt:
        return None

    # Call the API for summarization
    summary_text = await _call_microcompact_api(
        prompt, context, query_source
    )

    if summary_text is None:
        return None

    # Apply the summary
    return _apply_api_summary(
        messages, older_items, summary_text
    )


def _build_summarize_prompt(
    items: list[dict[str, Any]],
) -> str:
    """Build a summarization prompt for the microcompact API call."""
    parts: list[str] = [
        "Summarize the following tool results from a previous coding session. "
        "Focus on: decisions made, file paths modified, key outputs, and any errors. "
        "Be concise. Do not repeat boilerplate. Use bullet points.\n",
    ]

    # Group items by tool type
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        tool = item.get("tool_name", "unknown")
        by_tool.setdefault(tool, []).append(item)

    for tool_name, tool_items in sorted(by_tool.items()):
        parts.append(f"\n## {tool_name} results:")
        for item in tool_items[:10]:  # Cap per tool
            content = item.get("content_text", "")
            # Truncate long content in the prompt itself
            if len(content) > 3000:
                content = content[:1500] + "\n... [content truncated] ...\n" + content[-1500:]
            parts.append(f"\n---\n{content}")

    parts.append(
        "\n\nProvide your summary. The summary should be concise "
        "(no more than 500 tokens). Focus on actionable information."
    )
    return "\n".join(parts)


async def _call_microcompact_api(
    prompt: str,
    context: Any,
    query_source: str,
) -> Optional[str]:
    """Call the Claude API for microcompact summarization.

    Uses a lightweight model call. Handles timeouts gracefully.
    """
    try:
        from hare.services.api.client import call_model_api
    except ImportError:
        log_event(
            "tengu_microcompact_api_unavailable",
            {"reason": "client_not_importable", "query_source": query_source},
        )
        return None

    model = os.environ.get(
        "CLAUDE_CODE_MICROCOMPACT_MODEL",
        os.environ.get("CLAUDE_CODE_MODEL", "claude-sonnet-4-6-20250501"),
    )

    try:
        result = await asyncio.wait_for(
            call_model_api(
                messages=[
                    {"role": "user", "content": prompt},
                ],
                system_prompt=[
                    "You are a micro-compaction assistant. Summarize tool results concisely. "
                    "Focus on useful information: file paths, decisions, errors, key outputs. "
                    "Output only the summary text — no preamble, no meta-commentary."
                ],
                model=model,
                tools=[],
                thinking_config=None,
                max_tokens=MICROCOMPACT_MAX_OUTPUT_TOKENS,
                stream=False,
            ),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        log_event(
            "tengu_microcompact_api_timeout",
            {"model": model, "query_source": query_source},
        )
        return None
    except Exception as exc:
        log_event(
            "tengu_microcompact_api_error",
            {"error": str(exc), "model": model, "query_source": query_source},
        )
        return None

    return _extract_text_from_response(result)


def _extract_text_from_response(result: Any) -> Optional[str]:
    """Extract text content from a model API response."""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "\n".join(texts) if texts else None
        # Check nested message.content
        msg = result.get("message", {})
        if isinstance(msg, dict):
            inner = msg.get("content", "")
            if isinstance(inner, str):
                return inner
            if isinstance(inner, list):
                texts = [
                    b.get("text", "")
                    for b in inner
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                return "\n".join(texts) if texts else None
    # Object with attributes
    content_attr = getattr(result, "content", None)
    if content_attr is not None:
        if isinstance(content_attr, str):
            return content_attr
        if isinstance(content_attr, list):
            texts = [
                b.get("text", "")
                for b in content_attr
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "\n".join(texts) if texts else None
    # Check message attribute
    msg_attr = getattr(result, "message", None)
    if msg_attr is not None:
        return _extract_text_from_response(msg_attr)
    return None


def _apply_api_summary(
    messages: list[dict[str, Any]],
    compacted_items: list[dict[str, Any]],
    summary_text: str,
) -> dict[str, Any]:
    """Replace compacted tool results with an API-generated summary."""
    if not compacted_items or not summary_text:
        return {"messages": list(messages), "tokens_saved": 0}

    new_messages = list(messages)
    deleted_tool_ids: list[str] = []
    total_tokens_saved = 0

    # Group by message index
    items_by_msg: dict[int, list[dict[str, Any]]] = {}
    for item in compacted_items:
        idx = item.get("message_index", -1)
        if idx < 0 or idx >= len(new_messages):
            continue
        items_by_msg.setdefault(idx, []).append(item)

    # Collect all tool_use_ids being deleted and compute savings
    for item in compacted_items:
        tid = item.get("tool_use_id", "")
        if tid:
            deleted_tool_ids.append(tid)
        total_tokens_saved += item.get("tokens_after_truncation", 0)

    # Replace individual tool results with brief references
    for msg_idx, items in items_by_msg.items():
        msg = new_messages[msg_idx]
        inner = msg.get("message")
        if not isinstance(inner, dict):
            continue

        content = list(inner.get("content", []))
        if not isinstance(content, list):
            continue

        modified = False

        for item in items:
            block_idx = item.get("block_index", -1)
            if block_idx < 0 or block_idx >= len(content):
                continue

            block = content[block_idx]
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue

            # Skip already-compacted blocks (idempotency)
            if block.get("is_compacted"):
                continue

            content[block_idx] = {
                **block,
                "content": "[Tool result summarized, see compaction summary]",
                "is_compacted": True,
            }
            modified = True

        if modified:
            new_messages[msg_idx] = {
                **msg,
                "message": {**inner, "content": content},
            }

    # Estimate the correct insertion index from the last affected item
    valid_indices = [
        item["message_index"]
        for item in compacted_items
        if 0 <= item.get("message_index", -1) < len(new_messages)
    ]
    if valid_indices:
        insert_after_idx = min(max(valid_indices) + 1, len(new_messages))

        # Insert summary as a system message after the last compacted message
        summary_msg = build_microcompact_summary_message(
            summary_text,
            tokens_saved=total_tokens_saved,
            deleted_tool_count=len(deleted_tool_ids),
            trigger="api_summarize",
        )
        new_messages.insert(insert_after_idx, summary_msg)

    # Add cache control breakpoints
    new_messages = _add_cache_breakpoints(new_messages)

    result: dict[str, Any] = {
        "messages": new_messages,
        "tokens_saved": total_tokens_saved,
        "compaction_info": {
            "trigger": "api_summarize",
            "deleted_tool_ids": deleted_tool_ids,
            "baseline_cache_deleted_tokens": total_tokens_saved,
            "pending_cache_edits": {
                "trigger": "api_summarize",
                "deletedToolIds": deleted_tool_ids,
                "baselineCacheDeletedTokens": total_tokens_saved,
            },
        },
    }

    log_event(
        "tengu_microcompact_api_summarize",
        {
            "trigger": "api_summarize",
            "tokens_saved": total_tokens_saved,
            "summary_length": len(summary_text),
            "deleted_tool_count": len(deleted_tool_ids),
        },
    )

    return result


# ---------------------------------------------------------------------------
# Truncation with cache_edits fallback
# ---------------------------------------------------------------------------


def _apply_truncation_with_cache_edits(
    messages: list[dict[str, Any]],
    compactable: list[dict[str, Any]],
    total_tokens_saved: int,
) -> dict[str, Any]:
    """Apply simple truncation but include cache_edits metadata."""
    del total_tokens_saved  # used for log context only in caller

    new_messages = list(messages)
    deleted_tool_ids: list[str] = []
    actual_tokens_saved = 0

    # Group by message index
    items_by_msg: dict[int, list[dict[str, Any]]] = {}
    for item in compactable:
        idx = item.get("message_index", -1)
        if idx < 0 or idx >= len(new_messages):
            continue  # skip invalid indices
        items_by_msg.setdefault(idx, []).append(item)

    for msg_idx, items in items_by_msg.items():
        msg = new_messages[msg_idx]
        inner = msg.get("message")
        if not isinstance(inner, dict):
            continue

        content = list(inner.get("content", []))
        if not isinstance(content, list):
            continue

        modified = False

        for item in items:
            block_idx = item.get("block_index", -1)
            if block_idx < 0 or block_idx >= len(content):
                continue

            block = content[block_idx]
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue

            # Skip already-compacted blocks (idempotency)
            if block.get("is_compacted"):
                continue

            token_count = item.get("token_count", 0)
            content_text = item.get("content_text", "")
            if not content_text or not isinstance(content_text, str):
                continue

            truncated = (
                content_text[:500]
                + f"\n\n[... truncated {token_count} tokens ...]"
            )
            content[block_idx] = {**block, "content": truncated, "is_compacted": True}
            actual_tokens_saved += item.get("tokens_after_truncation", 0)
            modified = True
            tid = item.get("tool_use_id", "")
            if tid:
                deleted_tool_ids.append(tid)

        if modified:
            new_messages[msg_idx] = {
                **msg,
                "message": {**inner, "content": content},
            }

    # Add cache control breakpoints
    new_messages = _add_cache_breakpoints(new_messages)

    result: dict[str, Any] = {
        "messages": new_messages,
        "tokens_saved": actual_tokens_saved,
    }

    if (
        feature("CACHED_MICROCOMPACT")
        and actual_tokens_saved >= CACHE_EDITS_MIN_TOKEN_THRESHOLD
    ):
        result["compaction_info"] = {
            "trigger": "auto_truncate",
            "deleted_tool_ids": deleted_tool_ids,
            "baseline_cache_deleted_tokens": actual_tokens_saved,
            "pending_cache_edits": {
                "trigger": "auto_truncate",
                "deletedToolIds": deleted_tool_ids,
                "baselineCacheDeletedTokens": actual_tokens_saved,
            },
        }

    return result


# ---------------------------------------------------------------------------
# Cache control breakpoints
# ---------------------------------------------------------------------------


def _add_cache_breakpoints(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add cache control breakpoints to messages for prompt caching.

    Places ephemeral cache breakpoints on the last message, a message
    near the compaction boundary, and any system messages that serve
    as anchors for future cache reads.

    Mirrors the strategy in services/api/claude.py:add_cache_breakpoints.
    """
    result = list(messages)
    if not result:
        return result

    # Cache breakpoint on last message (cache up to here)
    last_msg = result[-1]
    if isinstance(last_msg, dict):
        last_msg["cache_control"] = {"type": "ephemeral"}

    # Cache breakpoint on the microcompact_summary message (2nd-to-last
    # or wherever it sits) so the summary itself is in cache for reuse.
    for i in range(len(result) - 2, -1, -1):
        if result[i].get("subtype") == "microcompact_summary":
            result[i]["cache_control"] = {"type": "ephemeral"}
            break

    # Cache breakpoint on the first message of the kept tail (3rd-to-last
    # of non-compacted messages) — preserve cache for the active context.
    if len(result) >= 3:
        result[-3]["cache_control"] = {"type": "ephemeral"}

    return result


# ---------------------------------------------------------------------------
# Microcompact summary message builder
# ---------------------------------------------------------------------------


def build_microcompact_summary_message(
    summary_text: str,
    *,
    tokens_saved: int = 0,
    deleted_tool_count: int = 0,
    trigger: str = "auto",
) -> dict[str, Any]:
    """Build a user-visible summary message describing what was compacted.

    Returns a dict suitable for insertion into the message stream as a
    microcompact boundary annotation.
    """
    meta_parts: list[str] = []
    if tokens_saved > 0:
        meta_parts.append(f"freed ~{tokens_saved} tokens")
    if deleted_tool_count > 0:
        meta_parts.append(f"cleared {deleted_tool_count} tool results")
    meta = ", ".join(meta_parts) if meta_parts else "compacted context"

    return {
        "type": "system",
        "subtype": "microcompact_summary",
        "trigger": trigger,
        "message": {
            "role": "system",
            "content": (
                f"<conversation_history_summary>\n"
                f"{summary_text}\n"
                f"\n[{meta}]\n"
                f"</conversation_history_summary>"
            ),
        },
        "tokens_saved": tokens_saved,
        "deleted_tool_count": deleted_tool_count,
    }


# ---------------------------------------------------------------------------
# Merge / combine multiple compaction results
# ---------------------------------------------------------------------------


def merge_compaction_results(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge multiple micro-compaction passes into one unified result.

    The last result's messages become the final messages. Token savings
    and deleted tool IDs are summed across all passes. compaction_info
    is taken from the last pass that had one.

    Returns a dict with messages, tokens_saved, and optional compaction_info.
    """
    if not results:
        return {"messages": [], "tokens_saved": 0}

    total_tokens_saved = 0
    all_deleted_tool_ids: list[str] = []
    last_compaction_info: Optional[dict[str, Any]] = None
    final_messages: list[dict[str, Any]] = []

    for r in results:
        if not isinstance(r, dict):
            continue
        total_tokens_saved += r.get("tokens_saved", 0)
        deleted = r.get("compaction_info", {}).get("deleted_tool_ids", [])
        if isinstance(deleted, list):
            all_deleted_tool_ids.extend(deleted)
        # Track last compaction_info seen
        ci = r.get("compaction_info") or r.get("compactionInfo")
        if ci is not None:
            last_compaction_info = ci
        # Last result's messages win
        msgs = r.get("messages")
        if msgs is not None:
            final_messages = list(msgs)

    # Deduplicate tool IDs while preserving order
    seen: set[str] = set()
    unique_ids = [tid for tid in all_deleted_tool_ids if not (tid in seen or seen.add(tid))]  # type: ignore[func-returns-value]

    merged: dict[str, Any] = {
        "messages": final_messages,
        "tokens_saved": total_tokens_saved,
    }

    if last_compaction_info is not None:
        merged["compaction_info"] = {
            **last_compaction_info,
            "deleted_tool_ids": unique_ids,
            "baseline_cache_deleted_tokens": total_tokens_saved,
            "pending_cache_edits": {
                "trigger": last_compaction_info.get("trigger", "auto"),
                "deletedToolIds": unique_ids,
                "baselineCacheDeletedTokens": total_tokens_saved,
            },
        }

    return merged


# ---------------------------------------------------------------------------
# Full pipeline orchestrator
# ---------------------------------------------------------------------------


async def apply_microcompact_pipeline(
    messages: list[dict[str, Any]],
    context: Any = None,
    query_source: str = "",
    *,
    config: Optional[MicroCompactConfig] = None,
    threshold_tokens: int = 0,
) -> dict[str, Any]:
    """Full micro-compaction pipeline orchestrator.

    Runs all applicable micro-compaction strategies in sequence:
      1. Time-based clearing (if enabled and triggered)
      2. Selective retention (preserve important tool results)
      3. API-based summarization (if CACHED_MICROCOMPACT is active)
      4. Truncation fallback

    Each stage works on the output of the previous stage, accumulating
    token savings and compaction metadata.

    Returns a dict with messages, tokens_saved, compaction_info, and
    an ordered record of which stages applied and how much they saved.
    """
    cfg = config or get_microcompact_config()
    effective_threshold = threshold_tokens or cfg.threshold_tokens

    if not cfg.enabled or len(messages) <= cfg.keep_recent:
        return {
            "messages": list(messages),
            "tokens_saved": 0,
            "stages": [],
            "compaction_info": None,
        }

    working_messages = list(messages)
    results_parts: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []

    # Stage 1: Time-based clearing
    if cfg.time_based_enabled:
        try:
            time_result = await _try_time_based_microcompact_with_config(
                working_messages, context, cfg
            )
        except Exception:
            time_result = None
        if time_result is not None:
            stages.append(
                {
                    "stage": "time_based",
                    "tokens_saved": time_result.get("tokens_saved", 0),
                }
            )
            results_parts.append(time_result)
            working_messages = list(time_result.get("messages", working_messages))

    # Stage 2: Selective retention
    try:
        selective_result = await selective_retention_microcompact(
            working_messages,
            threshold_tokens=effective_threshold,
            keep_recent=cfg.keep_recent,
        )
    except Exception:
        selective_result = {"messages": working_messages, "tokens_saved": 0}

    if selective_result.get("tokens_saved", 0) > 0:
        stages.append(
            {
                "stage": "selective_retention",
                "tokens_saved": selective_result.get("tokens_saved", 0),
            }
        )
        results_parts.append(selective_result)
        working_messages = list(selective_result.get("messages", working_messages))

    # Stage 3: API-based summarization (only when fully enabled)
    if cfg.api_summarize_enabled:
        try:
            compactable = _identify_compactable_blocks(
                working_messages, effective_threshold
            )
        except Exception:
            compactable = []

        api_target = [
            item for item in compactable
            if item.get("is_older")
        ]
        total = sum(item["token_count"] for item in api_target)

        if total >= cfg.api_summarize_threshold:
            try:
                api_result = await _try_api_microcompact_summarize(
                    working_messages, context, compactable, query_source
                )
            except Exception:
                api_result = None
            if api_result is not None:
                stages.append(
                    {
                        "stage": "api_summarize",
                        "tokens_saved": api_result.get("tokens_saved", 0),
                    }
                )
                results_parts.append(api_result)
                working_messages = list(api_result.get("messages", working_messages))

    # Stage 4: Truncation fallback (always available)
    try:
        compactable_final = _identify_compactable_blocks(
            working_messages, effective_threshold
        )
    except Exception:
        compactable_final = []

    if compactable_final:
        trunc_result = _apply_truncation_with_cache_edits(
            working_messages,
            [item for item in compactable_final if item.get("is_older")],
            sum(item["tokens_after_truncation"] for item in compactable_final if item.get("is_older")),
        )
        trunc_tokens_saved = trunc_result.get("tokens_saved", 0)
        if trunc_tokens_saved > 0:
            stages.append(
                {
                    "stage": "truncation",
                    "tokens_saved": trunc_tokens_saved,
                }
            )
            results_parts.append(trunc_result)
            working_messages = list(trunc_result.get("messages", working_messages))

    # Merge all results
    merged = merge_compaction_results(results_parts)
    merged["messages"] = working_messages
    merged["stages"] = stages

    log_event(
        "tengu_microcompact_pipeline",
        {
            "stages": [s["stage"] for s in stages],
            "total_tokens_saved": merged.get("tokens_saved", 0),
            "message_count_before": len(messages),
            "message_count_after": len(working_messages),
        },
    )

    return merged


# ---------------------------------------------------------------------------
# Time-based microcompact with custom config
# ---------------------------------------------------------------------------


async def _try_time_based_microcompact_with_config(
    messages: list[dict[str, Any]],
    context: Any,
    config: MicroCompactConfig,
) -> Optional[dict[str, Any]]:
    """Time-based microcompact using the central MicroCompactConfig.

    This is the config-aware variant that does NOT require importing
    time_based_mc_config. Instead it uses the settings from MicroCompactConfig
    directly, which makes it suitable for programmatic control.
    """
    try:
        from hare.services.compact.time_based_mc_config import (
            evaluate_time_based_trigger,
            maybe_time_based_microcompact as time_based_mc,
            get_time_based_mc_config,
        )

        # Prefer the full growthbook-backed impl when available
        gb_config = get_time_based_mc_config()
        if gb_config.enabled:
            tb_result = time_based_mc(messages, query_source=None)
            if tb_result is not None:
                new_messages = list(tb_result.get("messages", messages))
                tokens_saved = tb_result.get("tokens_saved", 0)
                return _wrap_time_based_result(new_messages, tokens_saved, "time_gap_gb")
    except ImportError:
        pass

    # Fallback: use the local config settings
    if not config.time_based_enabled:
        return None

    gap_threshold = config.gap_threshold_seconds
    keep_recent = config.keep_recent

    gaps = _find_time_gaps(messages, gap_threshold)
    if not gaps:
        return None

    first_gap_idx = gaps[0].index_before_gap
    if first_gap_idx <= keep_recent:
        return None

    # Identify compactable items before the gap
    compactable = _identify_compactable_blocks(messages, config.threshold_tokens)
    items_to_compact = [
        item for item in compactable
        if item["message_index"] < first_gap_idx
        and item["message_index"] < len(messages) - keep_recent
    ]

    if not items_to_compact:
        return None

    return _apply_time_based_clearing(
        messages, items_to_compact, first_gap_idx, gaps[0]
    )


def _wrap_time_based_result(
    messages: list[dict[str, Any]],
    tokens_saved: int,
    trigger: str,
) -> dict[str, Any]:
    """Wrap a raw time-based result into the standard microcompact shape."""
    result: dict[str, Any] = {
        "messages": messages,
        "tokens_saved": tokens_saved,
    }
    if (
        feature("CACHED_MICROCOMPACT")
        and tokens_saved >= CACHE_EDITS_MIN_TOKEN_THRESHOLD
    ):
        result["compaction_info"] = {
            "trigger": trigger,
            "deleted_tool_ids": [],
            "baseline_cache_deleted_tokens": tokens_saved,
            "pending_cache_edits": {
                "trigger": trigger,
                "deletedToolIds": [],
                "baselineCacheDeletedTokens": tokens_saved,
            },
        }
    return result


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total token count for messages."""
    total = 0
    for msg in messages:
        total += _estimate_msg_tokens(msg)
        total += 4  # per-message overhead
    return total


def _estimate_msg_tokens(msg: dict[str, Any]) -> int:
    """Estimate tokens for a single message dict."""
    msg_type = msg.get("type", "")
    if msg_type not in ("user", "assistant", "system"):
        return 0
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, str):
        return estimate_tokens(content)
    total = 0
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                total += estimate_tokens(block.get("text", ""))
            elif block_type == "tool_result":
                rc = block.get("content", "")
                if isinstance(rc, str):
                    total += estimate_tokens(rc)
                elif isinstance(rc, list):
                    for r in rc:
                        if isinstance(r, dict):
                            total += estimate_tokens(r.get("text", ""))
            elif block_type in ("image", "document"):
                total += IMAGE_MAX_TOKEN_SIZE
            elif block_type == "thinking":
                total += estimate_tokens(block.get("thinking", ""))
            elif block_type == "tool_use":
                total += estimate_tokens(str(block.get("input", {})))
    return total


def rough_token_count_estimation(text: str) -> int:
    """Rough token count estimation for a text string."""
    return estimate_tokens(text)


# ---------------------------------------------------------------------------
# Microcompact boundary / cache-deletion signal helpers
# ---------------------------------------------------------------------------


def build_pending_cache_edits(
    *,
    trigger: str = "auto",
    deleted_tool_ids: Optional[list[str]] = None,
    baseline_cache_deleted_tokens: int = 0,
) -> PendingCacheEdits:
    """Build a PendingCacheEdits instance for the query loop."""
    return PendingCacheEdits(
        trigger=trigger,
        deleted_tool_ids=list(deleted_tool_ids or []),
        baseline_cache_deleted_tokens=baseline_cache_deleted_tokens,
    )


def should_fire_cache_edits(
    tokens_saved: int,
    deleted_tool_count: int = 0,
    *,
    min_tokens: int = CACHE_EDITS_MIN_TOKEN_THRESHOLD,
) -> bool:
    """Determine if cache edits should be emitted based on savings."""
    return tokens_saved >= min_tokens or deleted_tool_count > 0


def get_cache_edit_signal(
    result: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Extract the pendingCacheEdits signal from a microcompact result.

    This mirrors the extraction logic in query/core.py's
    _extract_pending_cache_edits but operates directly on the dict shape.
    """
    compaction_info = result.get("compaction_info") or result.get("compactionInfo")
    if compaction_info is None:
        return None

    pending = compaction_info.get("pending_cache_edits") or compaction_info.get(
        "pendingCacheEdits"
    )
    if pending is None:
        return None

    return {
        "trigger": pending.get("trigger", "auto"),
        "deletedToolIds": pending.get("deletedToolIds", []),
        "baselineCacheDeletedTokens": pending.get("baselineCacheDeletedTokens", 0),
    }


# ---------------------------------------------------------------------------
# Exports (used by services/compact/__init__.py and query/deps.py)
# ---------------------------------------------------------------------------

__all__ = [
    # Main entry points
    "microcompact_messages",
    "apply_microcompact_pipeline",
    "selective_retention_microcompact",
    # Pre-flight / gating
    "should_microcompact",
    "estimate_compact_savings",
    "dry_run_microcompact",
    # Block extraction
    "compactable_tool_blocks",
    "tokens_to_compact",
    # Token estimation
    "estimate_message_tokens",
    "rough_token_count_estimation",
    # Result types
    "MicroCompactResult",
    "CompactionInfo",
    "PendingCacheEdits",
    "TimeGapInfo",
    "MicroCompactConfig",
    # Config
    "get_microcompact_config",
    # Cache edits helpers
    "build_pending_cache_edits",
    "should_fire_cache_edits",
    "get_cache_edit_signal",
    # Summary / merge
    "build_microcompact_summary_message",
    "merge_compaction_results",
    # Constants
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "COMPACTABLE_TOOLS",
    "PRESERVE_TOOLS",
    "CACHE_EDITS_MIN_TOKEN_THRESHOLD",
    "DEFAULT_KEEP_RECENT",
    "DEFAULT_API_SUMMARIZE_THRESHOLD",
    "DEFAULT_TIME_GAP_THRESHOLD_SECONDS",
]
