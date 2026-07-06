"""
GrowthBook config for time-based microcompact.

Port of: src/services/compact/timeBasedMCConfig.ts

Triggers content-clearing microcompact when the gap since the last main-loop
assistant message exceeds a threshold — the server-side prompt cache has
almost certainly expired, so the full prefix will be rewritten anyway.
Clearing old tool results before the request shrinks what gets rewritten.

Runs BEFORE the API call (in microcompactMessages, upstream of callModel)
so the shrunk prompt is what actually gets sent. Running after the first
miss would only help subsequent turns.

Main thread only — subagents have short lifetimes where gap-based eviction
doesn't apply.
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from hare.constants.query_source import QuerySource
from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale
from hare.services.token_estimation import estimate_tokens

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"
IMAGE_MAX_TOKEN_SIZE = 2000

# Tool names whose results are compactable.
# Mirrors COMPACTABLE_TOOLS in micro_compact.py and
# src/services/compact/microCompact.ts.
COMPACTABLE_TOOL_NAMES: frozenset[str] = frozenset(
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

# Default floor for keepRecent — slice(-0) returns the full array
# (paradoxically keeps everything), and clearing ALL results leaves the
# model with zero working context. Neither degenerate is sensible — always
# keep at least the last.
MIN_KEEP_RECENT = 1

# Maximum number of compactable items to enumerate in one pass — guards
# against pathological conversations with tens of thousands of tool results.
MAX_COMPACTABLE_ITEMS = 10_000

# When tokens saved exceed this threshold, also emit cache_edit metadata
# so the query loop can signal the API that cached content shifted.
CACHE_EDITS_MIN_TOKEN_THRESHOLD = 500

# Seconds between consecutive gap scans — used internally for short-circuit
# evaluation when messages have no usable timestamps.
MIN_GAP_DETECTION_WINDOW_SECONDS = 5.0

# Kilobytes limit for per-block content that we attempt to token-estimate.
# Large binary blobs (e.g., base64-encoded images inlined in tool results)
# are skipped rather than spending cycles on them.
MAX_CONTENT_SIZE_FOR_ESTIMATION = 1_000_000  # 1 MB


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class TimeBasedMCConfig:
    """GrowthBook config for time-based microcompact.

    Attributes:
        enabled: Master switch. When False, time-based microcompact is a no-op.
        gap_threshold_minutes: Trigger when (now − last assistant timestamp)
            exceeds this many minutes. 60 is the safe choice: the server's 1h
            cache TTL is guaranteed expired for all users, so we never force
            a miss that wouldn't have happened.
        keep_recent: Keep this many most-recent compactable tool results.
            When set, takes priority over any default; older results are
            cleared.
    """

    enabled: bool = False
    gap_threshold_minutes: int = 60
    keep_recent: int = 5


_DEFAULTS = TimeBasedMCConfig()


# ---------------------------------------------------------------------------
# Time-gap info dataclass
# ---------------------------------------------------------------------------


@dataclass
class TimeGapInfo:
    """Information about a time gap between consecutive messages.

    Attributes:
        gap_seconds: Duration of the gap in seconds.
        index_before_gap: Index (in message list) of the message just before
            the gap — i.e. the last message sent before the pause.
        token_count_before_gap: Cumulative token count up to and including
            the message at index_before_gap.
        start_timestamp: Unix timestamp of the message before the gap.
        end_timestamp: Unix timestamp of the message after the gap.
    """

    gap_seconds: float = 0.0
    index_before_gap: int = 0
    token_count_before_gap: int = 0
    start_timestamp: float = 0.0
    end_timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Config lookup
# ---------------------------------------------------------------------------


def get_time_based_mc_config() -> TimeBasedMCConfig:
    """Read the time-based microcompact config from GrowthBook.

    Hoist the GB read so exposure fires on every eval path, not just when
    the caller's other conditions (querySource, messages.length) pass.
    """
    raw = get_feature_value_cached_may_be_stale("tengu_slate_heron", None)
    if isinstance(raw, dict):
        return TimeBasedMCConfig(
            enabled=bool(raw.get("enabled", _DEFAULTS.enabled)),
            gap_threshold_minutes=int(
                raw.get("gapThresholdMinutes", _DEFAULTS.gap_threshold_minutes)
            ),
            keep_recent=int(raw.get("keepRecent", _DEFAULTS.keep_recent)),
        )
    return _DEFAULTS


# ---------------------------------------------------------------------------
# Timestamp extraction
# ---------------------------------------------------------------------------


def _extract_timestamp(msg: dict[str, Any]) -> Optional[float]:
    """Extract a Unix timestamp (seconds) from a message dict.

    Handles:
      - Direct ``timestamp`` field — unix seconds (<= 1e12) or milliseconds (> 1e12).
      - ISO-8601 strings via ``timestamp_iso`` or ``created_at`` fields.
      - ``datetime.datetime`` objects.
      - String representations that `float()` can parse.

    Returns None when no usable timestamp is found or when the value is
    unparseable, negative, or implausibly in the future.
    """
    # Direct numeric timestamp
    ts = msg.get("timestamp")
    if ts is not None:
        try:
            if isinstance(ts, datetime.datetime):
                return ts.timestamp()
            if isinstance(ts, (int, float)):
                if ts <= 0:
                    return None
                # Milliseconds → seconds
                if ts > 1_000_000_000_000:
                    return ts / 1000.0
                return float(ts)
            if isinstance(ts, str):
                try:
                    val = float(ts)
                    if val <= 0:
                        return None
                    if val > 1_000_000_000_000:
                        return val / 1000.0
                    return val
                except (ValueError, TypeError):
                    pass
        except (TypeError, OverflowError):
            pass

    # ISO-8601 timestamp strings
    for key in ("timestamp_iso", "created_at", "timestampIso", "createdAt"):
        iso = msg.get(key)
        if isinstance(iso, str) and iso:
            try:
                dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
                return dt.timestamp()
            except (ValueError, TypeError, AttributeError):
                pass

    return None


def _get_last_assistant_timestamp(
    messages: list[dict[str, Any]],
) -> Optional[float]:
    """Find the timestamp of the most recent assistant message.

    Returns a Unix timestamp (seconds) or None if no assistant message exists.
    """
    for msg in reversed(messages):
        if msg.get("type") == "assistant":
            ts = _extract_timestamp(msg)
            if ts is not None:
                return ts
    return None


def _get_last_user_timestamp(
    messages: list[dict[str, Any]],
) -> Optional[float]:
    """Find the timestamp of the most recent user message.

    Returns a Unix timestamp (seconds) or None if no user message exists.
    Useful as a fallback when assistant messages lack timestamps but user
    messages carry them (e.g. SDK-sourced messages).
    """
    for msg in reversed(messages):
        if msg.get("type") == "user":
            ts = _extract_timestamp(msg)
            if ts is not None:
                return ts
    return None


def _get_last_message_timestamp(
    messages: list[dict[str, Any]],
) -> Optional[float]:
    """Find the timestamp of the last message of any type.

    Returns a Unix timestamp (seconds) or None if no message carries
    a usable timestamp.
    """
    for msg in reversed(messages):
        ts = _extract_timestamp(msg)
        if ts is not None:
            return ts
    return None


# ---------------------------------------------------------------------------
# Time-gap detection
# ---------------------------------------------------------------------------


def _find_time_gaps(
    messages: list[dict[str, Any]],
    gap_threshold_seconds: float,
) -> list[TimeGapInfo]:
    """Find significant time gaps between consecutive messages.

    Scans the message list sequentially, computing the gap between each
    consecutive pair. A gap is "significant" when its duration exceeds
    ``gap_threshold_seconds``.

    Gaps are returned in encounter order (earliest first).  Runs in O(n)
    and short-circuits after ``MAX_COMPACTABLE_ITEMS`` messages to avoid
    pathological OOM on giant conversation histories.
    """
    if gap_threshold_seconds <= 0:
        return []

    gaps: list[TimeGapInfo] = []
    token_count_so_far = 0
    limit = min(len(messages), MAX_COMPACTABLE_ITEMS)

    for i in range(limit - 1):
        token_count_so_far += _estimate_msg_tokens(messages[i])

        ts_current = _extract_timestamp(messages[i])
        ts_next = _extract_timestamp(messages[i + 1])

        if ts_current is None or ts_next is None:
            continue

        # Ignore negative gaps (clock skew / out-of-order messages)
        gap = ts_next - ts_current
        if gap < MIN_GAP_DETECTION_WINDOW_SECONDS:
            continue

        if gap >= gap_threshold_seconds:
            gaps.append(
                TimeGapInfo(
                    gap_seconds=gap,
                    index_before_gap=i,
                    token_count_before_gap=token_count_so_far,
                    start_timestamp=ts_current,
                    end_timestamp=ts_next,
                )
            )

    return gaps


# ---------------------------------------------------------------------------
# Message token estimation
# ---------------------------------------------------------------------------


def _estimate_msg_tokens(msg: dict[str, Any]) -> int:
    """Estimate token count for a single message dict.

    Accounts for all block types: text, tool_use, tool_result, image,
    document, and thinking.  Skips content exceeding
    ``MAX_CONTENT_SIZE_FOR_ESTIMATION`` to avoid CPU spikes on inline blobs.
    """
    msg_type = msg.get("type", "")
    if msg_type not in ("user", "assistant", "system"):
        return 0

    message = msg.get("message", {})
    if not isinstance(message, dict):
        return 0

    content = message.get("content", [])
    if isinstance(content, str):
        return _safe_estimate(content)

    total = 0
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                total += _safe_estimate(str(block.get("text", "")))
            elif block_type == "tool_use":
                input_val = block.get("input", {})
                total += _safe_estimate(str(input_val))
                total += 20  # structural overhead for tool_use JSON
            elif block_type == "tool_result":
                rc = block.get("content", "")
                total += _calculate_block_tokens(rc)
            elif block_type in ("image", "document"):
                total += IMAGE_MAX_TOKEN_SIZE
            elif block_type == "thinking":
                total += _safe_estimate(str(block.get("thinking", "")))
            elif block_type == "redacted_thinking":
                data = block.get("data", "")
                if isinstance(data, str):
                    total += _safe_estimate(data)

    return total


def _safe_estimate(text: str) -> int:
    """Estimate tokens for text, clamping large input to avoid CPU spikes."""
    if not text:
        return 0
    if len(text) > MAX_CONTENT_SIZE_FOR_ESTIMATION:
        # Cap at 1 MB — beyond that, the token count is dominated by this
        # block anyway and we can approximate.
        return max(1, MAX_CONTENT_SIZE_FOR_ESTIMATION // 4)
    return estimate_tokens(text)


def _calculate_block_tokens(content: Any) -> int:
    """Estimate tokens for a single content block (string or list)."""
    if isinstance(content, str):
        return _safe_estimate(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                total += _safe_estimate(str(item.get("text", "")))
            elif item.get("type") in ("image", "document"):
                total += IMAGE_MAX_TOKEN_SIZE
        return total
    return 0


# ---------------------------------------------------------------------------
# Tool name inference
# ---------------------------------------------------------------------------


def _infer_tool_name(block: dict[str, Any]) -> str:
    """Try to infer the tool name from a tool_result block.

    tool_result blocks don't carry the tool name directly in the Anthropic
    API schema, but various transport layers attach metadata.  We check:
      1. ``tool_name`` / ``toolName`` on the block itself.
      2. ``_tool_meta.name`` — metadata attached by the bridge.
      3. ``name`` on the block — some serializers flatten it.
      4. ``tool_use_id`` prefix heuristic — e.g. "toolu_bash_..." patterns
         are rare, but we check as a last resort.
    """
    # Direct attributes
    for key in ("tool_name", "toolName"):
        val = block.get(key, "")
        if isinstance(val, str) and val:
            return val

    # Metadata bag
    tool_meta = block.get("_tool_meta", {})
    if isinstance(tool_meta, dict):
        name = tool_meta.get("name", "")
        if isinstance(name, str) and name:
            return name

    # Flattened name
    name = block.get("name", "")
    if isinstance(name, str) and name and name != block.get("type", ""):
        return name

    return ""


# ---------------------------------------------------------------------------
# Compactable block identification
# ---------------------------------------------------------------------------


def _identify_compactable_blocks(
    messages: list[dict[str, Any]],
    threshold_tokens: int = 0,
    *,
    config: Optional[TimeBasedMCConfig] = None,
) -> list[dict[str, Any]]:
    """Walk messages and collect compactable tool-result blocks with metadata.

    Returns a list of dicts, each describing one compactable block:

        - message_index (int): position in the messages list
        - block_index (int): position within the message's content list
        - block (dict): the original content block reference
        - tool_name (str): inferred tool name (may be empty for unknown tools)
        - tool_use_id (str): the ``tool_use_id`` that links result to use
        - content_text (str): the text content for token estimation
        - token_count (int): estimated token count
        - is_older (bool): True when the message falls outside the keep_recent
          window (i.e. it is eligible for compaction)

    When ``config`` is provided, its ``keep_recent`` governs the
    is_older determination.  Otherwise all blocks are considered older.
    """
    compactable: list[dict[str, Any]] = []
    keep_recent = config.keep_recent if config else 0

    for i, msg in enumerate(messages):
        if i >= MAX_COMPACTABLE_ITEMS:
            break

        is_older = i < max(0, len(messages) - keep_recent)
        if msg.get("type") != "user":
            continue

        message = msg.get("message", {})
        if not isinstance(message, dict):
            continue

        content = message.get("content", [])
        if not isinstance(content, list):
            continue

        for j, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue

            tool_name = _infer_tool_name(block)
            # Skip known non-compactable tools; compact unknown tools by default.
            if tool_name and tool_name not in COMPACTABLE_TOOL_NAMES:
                continue

            result_content = block.get("content", "")
            if not isinstance(result_content, (str, list)):
                continue

            token_count = _calculate_tool_result_tokens(block)
            if threshold_tokens > 0 and token_count <= threshold_tokens:
                continue

            compactable.append(
                {
                    "message_index": i,
                    "block_index": j,
                    "block": block,
                    "tool_name": tool_name,
                    "tool_use_id": str(block.get("tool_use_id", "")),
                    "content_text": (
                        result_content
                        if isinstance(result_content, str)
                        else str(result_content)[:500]
                    ),
                    "token_count": token_count,
                    "is_older": is_older,
                }
            )

    return compactable


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------


def _is_main_thread_source(query_source: Optional[QuerySource]) -> bool:
    """Prefix-match because prompt_category may append ':outputStyle:<style>'.

    The bare 'repl_main_thread' is only used for the default output style.
    Other entry points that invoke microcompact (e.g. /context, /compact,
    analyze_context) pass a source or None for analysis-only purposes —
    they should NOT trigger time-based MC.
    """
    if not query_source:
        return False
    return query_source.startswith("repl_main_thread")


def evaluate_time_based_trigger(
    messages: list[dict[str, Any]],
    query_source: Optional[QuerySource],
) -> Optional[dict[str, Any]]:
    """Check whether the time-based trigger should fire for this request.

    Require an explicit main-thread query_source. Several callers
    (/context, /compact, analyze_context) invoke microcompact without a
    source for analysis-only purposes — they should not trigger.

    Returns a dict with 'gap_minutes' (float) and 'config' (TimeBasedMCConfig)
    when the trigger fires, or None when it doesn't (disabled, wrong source,
    under threshold, no prior assistant, unparseable timestamp).
    """
    config = get_time_based_mc_config()

    if not config.enabled or not _is_main_thread_source(query_source):
        return None

    last_ts = _get_last_assistant_timestamp(messages)
    if last_ts is None:
        last_ts = _get_last_user_timestamp(messages)
    if last_ts is None:
        last_ts = _get_last_message_timestamp(messages)
    if last_ts is None:
        return None

    now = time.time()
    gap_seconds = now - last_ts
    gap_minutes = gap_seconds / 60.0

    if gap_minutes < config.gap_threshold_minutes:
        return None

    return {"gap_minutes": gap_minutes, "config": config}


# ---------------------------------------------------------------------------
# Gate / pre-check functions
# ---------------------------------------------------------------------------


def should_time_based_microcompact(
    messages: list[dict[str, Any]],
    query_source: Optional[QuerySource] = None,
) -> bool:
    """Return True when time-based microcompact should be attempted.

    This is a lightweight gate suitable for callers that want to check
    eligibility before taking more expensive actions (e.g. identifying
    compactable blocks or computing token estimates).

    It calls ``evaluate_time_based_trigger`` internally so all the same
    guardrails apply (enabled, main thread, gap >= threshold).
    """
    try:
        return evaluate_time_based_trigger(messages, query_source) is not None
    except Exception:
        _logger.debug("should_time_based_microcompact: evaluation failed", exc_info=True)
        return False


def find_gap_cutoff_index(
    messages: list[dict[str, Any]],
    config: Optional[TimeBasedMCConfig] = None,
) -> int:
    """Find the message index at which time-based clearing should start.

    Uses multi-gap detection (``_find_time_gaps``) with the threshold from
    ``config``.  Returns the index of the first message AFTER the earliest
    significant gap, or -1 when no gap is found.

    Callers use this to determine which messages are "old enough" to clear.
    Messages at indices < cutoff are candidates for content clearing; messages
    at indices >= cutoff are preserved.
    """
    cfg = config or get_time_based_mc_config()
    if not cfg.enabled:
        return -1

    gap_threshold_seconds = cfg.gap_threshold_minutes * 60.0
    gaps = _find_time_gaps(messages, gap_threshold_seconds)
    if not gaps:
        return -1

    # The first significant gap is the most relevant — everything before it
    # was sent in a prior session whose server cache has expired.
    return gaps[0].index_before_gap + 1


# ---------------------------------------------------------------------------
# Tool-result token estimation
# ---------------------------------------------------------------------------


def _calculate_tool_result_tokens(block: dict[str, Any]) -> int:
    """Estimate tokens for a single tool_result block.

    Mirrors the frontend calculateToolResultTokens in microCompact.ts.
    """
    content = block.get("content", "")
    if not content:
        return 0

    if isinstance(content, str):
        return _safe_estimate(content)

    # Array of content blocks (text/image/document)
    if isinstance(content, list):
        total = 0
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                total += _safe_estimate(str(item.get("text", "")))
            elif item.get("type") in ("image", "document"):
                total += IMAGE_MAX_TOKEN_SIZE
        return total

    return 0


# ---------------------------------------------------------------------------
# Tool ID collection
# ---------------------------------------------------------------------------


def _collect_compactable_tool_ids(messages: list[dict[str, Any]]) -> list[str]:
    """Walk messages and collect tool_use IDs whose tool name is in
    COMPACTABLE_TOOL_NAMES, in encounter order.

    Shared by both time-based and cached microcompact paths.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in COMPACTABLE_TOOL_NAMES:
                tid = block.get("id")
                if tid and isinstance(tid, str) and tid not in seen:
                    ids.append(tid)
                    seen.add(tid)
    return ids


def _collect_compactable_tool_result_ids(
    messages: list[dict[str, Any]],
) -> tuple[list[str], dict[str, str]]:
    """Walk messages and collect compactable tool_result blocks' tool_use_ids.

    Returns a tuple of (ordered_ids, id_to_tool_name_map).
    Unlike ``_collect_compactable_tool_ids`` which walks assistant message
    tool_use blocks, this walks user message tool_result blocks and infers
    tool names via ``_infer_tool_name``.
    """
    ids: list[str] = []
    id_to_tool: dict[str, str] = {}
    seen: set[str] = set()

    for msg in messages:
        if msg.get("type") != "user":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if not tool_use_id or not isinstance(tool_use_id, str):
                continue
            if tool_use_id in seen:
                continue

            tool_name = _infer_tool_name(block)
            if tool_name and tool_name not in COMPACTABLE_TOOL_NAMES:
                continue

            ids.append(tool_use_id)
            id_to_tool[tool_use_id] = tool_name
            seen.add(tool_use_id)

    return ids, id_to_tool


# ---------------------------------------------------------------------------
# Time-based microcompact application
# ---------------------------------------------------------------------------


def maybe_time_based_microcompact(
    messages: list[dict[str, Any]],
    query_source: Optional[QuerySource] = None,
) -> Optional[dict[str, Any]]:
    """Time-based microcompact: when the gap since the last main-loop assistant
    message exceeds the configured threshold, content-clear all but the most
    recent N compactable tool results.

    Returns None when the trigger doesn't fire (disabled, wrong source, gap
    under threshold, nothing to clear) — caller falls through to other paths.

    Unlike cached MC, this mutates message content directly. The cache is
    cold, so there's no cached prefix to preserve via cache_edits.

    Returns a dict with 'messages' and optionally 'tokens_saved' when
    content was cleared.
    """
    try:
        trigger = evaluate_time_based_trigger(messages, query_source)
    except Exception:
        _logger.debug("maybe_time_based_microcompact: trigger evaluation failed", exc_info=True)
        return None

    if trigger is None:
        return None

    gap_minutes: float = trigger["gap_minutes"]
    config: TimeBasedMCConfig = trigger["config"]

    # Collect compactable tool IDs from both assistant tool_use blocks and
    # user tool_result blocks for complete coverage.
    compactable_ids = _collect_compactable_tool_ids(messages)
    result_ids, _id_to_tool = _collect_compactable_tool_result_ids(messages)

    # Merge, preserving encounter order and deduplicating.
    seen: set[str] = set()
    merged_ids: list[str] = []
    for tid in compactable_ids + result_ids:
        if tid not in seen:
            merged_ids.append(tid)
            seen.add(tid)

    # Floor at 1: slice(-0) returns the full array (paradoxically keeps
    # everything), and clearing ALL results leaves the model with zero
    # working context.
    keep_recent = max(MIN_KEEP_RECENT, config.keep_recent)
    keep_set: set[str] = set(merged_ids[-keep_recent:])
    clear_set: set[str] = {tid for tid in merged_ids if tid not in keep_set}

    if not clear_set:
        return None

    tokens_saved = 0
    new_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("type") != "user":
            new_messages.append(msg)
            continue

        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            new_messages.append(msg)
            continue

        touched = False
        new_content: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") in clear_set
                and block.get("content") != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                tokens_saved += _calculate_tool_result_tokens(block)
                touched = True
                new_content.append(
                    {**block, "content": TIME_BASED_MC_CLEARED_MESSAGE}
                )
            else:
                new_content.append(block)

        if touched:
            new_msg = {
                **msg,
                "message": {**msg.get("message", {}), "content": new_content},
            }
            new_messages.append(new_msg)
        else:
            new_messages.append(msg)

    if tokens_saved == 0:
        return None

    # Emit analytics event for observability.
    _log_time_based_event(
        gap_minutes=gap_minutes,
        tokens_saved=tokens_saved,
        tools_cleared=len(clear_set),
        tools_kept=len(keep_set),
        config=config,
    )

    result: dict[str, Any] = {
        "messages": new_messages,
        "tokens_saved": tokens_saved,
        "gap_minutes": gap_minutes,
        "tools_cleared": len(clear_set),
        "tools_kept": len(keep_set),
        "keep_recent": config.keep_recent,
        "gap_threshold_minutes": config.gap_threshold_minutes,
    }

    # Attach cache_edit metadata when token savings warrant it.
    if tokens_saved >= CACHE_EDITS_MIN_TOKEN_THRESHOLD:
        result["compaction_info"] = _build_time_based_compaction_info(
            clear_set=clear_set,
            tokens_saved=tokens_saved,
        )

    return result


# ---------------------------------------------------------------------------
# Multi-gap aware time-based microcompact
# ---------------------------------------------------------------------------


def maybe_time_based_microcompact_multi_gap(
    messages: list[dict[str, Any]],
    query_source: Optional[QuerySource] = None,
) -> Optional[dict[str, Any]]:
    """Time-based microcompact using multi-gap detection.

    Instead of only checking the gap since the last assistant message,
    this scans the full message history for significant time gaps and
    clears compactable results before the earliest gap.

    This is more aggressive than ``maybe_time_based_microcompact`` and is
    suitable when users have multiple long pauses in a conversation (e.g.
    overnight, then a morning session, then an afternoon session).

    Returns the same shape as ``maybe_time_based_microcompact``.
    """
    try:
        config = get_time_based_mc_config()
    except Exception:
        _logger.debug("maybe_time_based_microcompact_multi_gap: config failed", exc_info=True)
        return None

    if not config.enabled or not _is_main_thread_source(query_source):
        return None

    gap_threshold_seconds = config.gap_threshold_minutes * 60.0

    try:
        gaps = _find_time_gaps(messages, gap_threshold_seconds)
    except Exception:
        _logger.debug(
            "maybe_time_based_microcompact_multi_gap: gap detection failed",
            exc_info=True,
        )
        return None

    if not gaps:
        return None

    keep_recent = max(MIN_KEEP_RECENT, config.keep_recent)
    first_gap_idx = gaps[0].index_before_gap

    # If the first gap is within the keep_recent tail, there's nothing
    # meaningful to clear.
    if first_gap_idx < keep_recent:
        return None

    # Identify compactable blocks in the "old" section (before the gap
    # AND outside the keep_recent tail).
    try:
        compactable_blocks = _identify_compactable_blocks(
            messages, threshold_tokens=0, config=config
        )
    except Exception:
        _logger.debug(
            "maybe_time_based_microcompact_multi_gap: block identification failed",
            exc_info=True,
        )
        return None

    items_to_clear = [
        item
        for item in compactable_blocks
        if item["message_index"] <= first_gap_idx
        and item["message_index"] < max(0, len(messages) - keep_recent)
    ]

    if not items_to_clear:
        return None

    clear_ids: set[str] = {item["tool_use_id"] for item in items_to_clear if item["tool_use_id"]}
    keep_ids = set(compactable_ids[-keep_recent:] if (compactable_ids := [item["tool_use_id"] for item in compactable_blocks if item["tool_use_id"]]) else [])

    if not clear_ids:
        return None

    return _apply_content_clearing(
        messages=messages,
        clear_set=clear_ids,
        gap_minutes=gaps[0].gap_seconds / 60.0,
        config=config,
    )


# ---------------------------------------------------------------------------
# Content clearing application
# ---------------------------------------------------------------------------


def _apply_content_clearing(
    messages: list[dict[str, Any]],
    clear_set: set[str],
    gap_minutes: float,
    config: TimeBasedMCConfig,
) -> Optional[dict[str, Any]]:
    """Apply content clearing to messages for the given set of tool_use_ids.

    Shared by both single-gap and multi-gap paths.  Mutates message content
    by replacing compactable tool_result content with a marker message.
    """
    tokens_saved = 0
    new_messages: list[dict[str, Any]] = []
    cleared_count = 0

    for msg in messages:
        if msg.get("type") != "user":
            new_messages.append(msg)
            continue

        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            new_messages.append(msg)
            continue

        touched = False
        new_content: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            tool_use_id = block.get("tool_use_id")
            if (
                block.get("type") == "tool_result"
                and tool_use_id in clear_set
                and block.get("content") != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                # Skip content that is already cleared or is structurally
                # empty (an empty string "" is not the same as the cleared
                # marker, but clearing it saves nothing).
                current_content = block.get("content", "")
                if isinstance(current_content, str) and not current_content.strip():
                    new_content.append(block)
                    continue

                tokens_saved += _calculate_tool_result_tokens(block)
                touched = True
                cleared_count += 1
                new_content.append(
                    {**block, "content": TIME_BASED_MC_CLEARED_MESSAGE}
                )
            else:
                new_content.append(block)

        if touched:
            new_msg = {
                **msg,
                "message": {**msg.get("message", {}), "content": new_content},
            }
            new_messages.append(new_msg)
        else:
            new_messages.append(msg)

    if tokens_saved == 0:
        return None

    # Analytics
    _log_time_based_event(
        gap_minutes=gap_minutes,
        tokens_saved=tokens_saved,
        tools_cleared=cleared_count,
        tools_kept=0,  # caller should compute if needed
        config=config,
    )

    result: dict[str, Any] = {
        "messages": new_messages,
        "tokens_saved": tokens_saved,
        "gap_minutes": gap_minutes,
        "tools_cleared": cleared_count,
        "tools_kept": 0,
        "keep_recent": config.keep_recent,
        "gap_threshold_minutes": config.gap_threshold_minutes,
    }

    if tokens_saved >= CACHE_EDITS_MIN_TOKEN_THRESHOLD:
        result["compaction_info"] = _build_time_based_compaction_info(
            clear_set=clear_set,
            tokens_saved=tokens_saved,
        )

    return result


# ---------------------------------------------------------------------------
# Estimation / dry-run
# ---------------------------------------------------------------------------


def estimate_time_based_savings(
    messages: list[dict[str, Any]],
    query_source: Optional[QuerySource] = None,
) -> dict[str, Any]:
    """Estimate token savings from time-based microcompact without modifying messages.

    Returns a dict with:
        - would_trigger: whether the time-based trigger fires
        - gap_minutes: the gap since the last assistant message (None if no gap)
        - estimated_savings: tokens that would be saved
        - tools_to_clear: count of compactable tool results that would be cleared
        - tools_to_keep: count that would be preserved
        - keep_recent: the effective keep_recent value
        - gap_threshold_minutes: the threshold from config
        - breakdown_by_tool: per-tool-type savings estimates
    """
    try:
        trigger = evaluate_time_based_trigger(messages, query_source)
    except Exception:
        return {
            "would_trigger": False,
            "gap_minutes": None,
            "estimated_savings": 0,
            "tools_to_clear": 0,
            "tools_to_keep": 0,
            "keep_recent": 0,
            "gap_threshold_minutes": 0,
            "breakdown_by_tool": {},
        }

    if trigger is None:
        return {
            "would_trigger": False,
            "gap_minutes": None,
            "estimated_savings": 0,
            "tools_to_clear": 0,
            "tools_to_keep": 0,
            "keep_recent": get_time_based_mc_config().keep_recent,
            "gap_threshold_minutes": get_time_based_mc_config().gap_threshold_minutes,
            "breakdown_by_tool": {},
        }

    config: TimeBasedMCConfig = trigger["config"]
    gap_minutes: float = trigger["gap_minutes"]

    compactable_ids = _collect_compactable_tool_ids(messages)
    result_ids, id_to_tool = _collect_compactable_tool_result_ids(messages)

    # Merge and deduplicate
    seen: set[str] = set()
    merged_ids: list[str] = []
    for tid in compactable_ids + result_ids:
        if tid not in seen:
            merged_ids.append(tid)
            seen.add(tid)

    keep_recent = max(MIN_KEEP_RECENT, config.keep_recent)
    keep_set: set[str] = set(merged_ids[-keep_recent:])
    clear_set: set[str] = {tid for tid in merged_ids if tid not in keep_set}

    estimated_savings = 0
    breakdown: dict[str, int] = {}

    for msg in messages:
        if msg.get("type") != "user":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") in clear_set
                and block.get("content") != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                tokens = _calculate_tool_result_tokens(block)
                estimated_savings += tokens
                # Resolve tool name: prefer the block's own, then the id_to_tool map
                tool = _infer_tool_name(block) or id_to_tool.get(
                    str(block.get("tool_use_id", "")), "unknown"
                )
                breakdown[tool] = breakdown.get(tool, 0) + tokens

    return {
        "would_trigger": True,
        "gap_minutes": gap_minutes,
        "estimated_savings": estimated_savings,
        "tools_to_clear": len(clear_set),
        "tools_to_keep": len(keep_set),
        "keep_recent": config.keep_recent,
        "gap_threshold_minutes": config.gap_threshold_minutes,
        "breakdown_by_tool": breakdown,
    }


# ---------------------------------------------------------------------------
# Compaction info builder
# ---------------------------------------------------------------------------


def _build_time_based_compaction_info(
    clear_set: set[str],
    tokens_saved: int,
) -> dict[str, Any]:
    """Build compaction_info metadata for cache-edit signalling.

    Time-based MC differs from cached MC in that the cache is already cold —
    we don't need to preserve any prefix.  The cache_edit metadata here is
    informational for the query loop to track what was modified.
    """
    return {
        "trigger": "time_gap",
        "deleted_tool_ids": sorted(clear_set),
        "baseline_cache_deleted_tokens": tokens_saved,
        "pending_cache_edits": {
            "trigger": "time_gap",
            "deletedToolIds": sorted(clear_set),
            "baselineCacheDeletedTokens": tokens_saved,
        },
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def _log_time_based_event(
    *,
    gap_minutes: float,
    tokens_saved: int,
    tools_cleared: int,
    tools_kept: int,
    config: TimeBasedMCConfig,
) -> None:
    """Emit an analytics event when time-based microcompact fires.

    Uses fire-and-forget — failures in the analytics pipeline should never
    prevent MC from completing.
    """
    try:
        from hare.services.analytics import log_event

        log_event(
            "tengu_microcompact_time_based",
            {
                "trigger": "time_gap",
                "tokens_saved": tokens_saved,
                "gap_minutes": round(gap_minutes, 1),
                "tools_cleared": tools_cleared,
                "tools_kept": tools_kept,
                "keep_recent": config.keep_recent,
                "gap_threshold_minutes": config.gap_threshold_minutes,
                "enabled": config.enabled,
            },
        )
    except Exception:
        _logger.debug("_log_time_based_event: analytics logging failed", exc_info=True)


# ---------------------------------------------------------------------------
# Integration helper: full pre-request entry point
# ---------------------------------------------------------------------------


async def apply_time_based_microcompact(
    messages: list[dict[str, Any]],
    *,
    query_source: Optional[QuerySource] = None,
    tool_use_context: Any = None,
) -> dict[str, Any]:
    """Apply time-based microcompact before an API call.

    This is the primary entry point used by microcompact_messages() in
    micro_compact.py to check and apply the time-based trigger before
    falling through to cached or legacy microcompact paths.

    Returns a dict with:
        - 'messages': the (possibly modified) message list
        - 'applied': bool indicating whether time-based MC fired
        - 'result': the result dict from maybe_time_based_microcompact, or None
    """
    del tool_use_context  # reserved for future use

    try:
        result = maybe_time_based_microcompact(messages, query_source)
    except Exception:
        _logger.warning(
            "apply_time_based_microcompact: unexpectedly failed, "
            "returning original messages",
            exc_info=True,
        )
        return {"messages": messages, "applied": False, "result": None}

    if result is None:
        return {"messages": messages, "applied": False, "result": None}

    return {
        "messages": result["messages"],
        "applied": True,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Multi-gap aware integration entry point
# ---------------------------------------------------------------------------


async def apply_time_based_microcompact_multi_gap(
    messages: list[dict[str, Any]],
    *,
    query_source: Optional[QuerySource] = None,
) -> dict[str, Any]:
    """Apply multi-gap-aware time-based microcompact before an API call.

    This is an alternative entry point that uses ``_find_time_gaps`` for
    multi-gap detection instead of the simpler last-assistant-timestamp
    heuristic.  Suitable for callers that want deeper time analysis.

    Returns the same shape as ``apply_time_based_microcompact``.
    """
    try:
        result = maybe_time_based_microcompact_multi_gap(messages, query_source)
    except Exception:
        _logger.warning(
            "apply_time_based_microcompact_multi_gap: unexpectedly failed, "
            "returning original messages",
            exc_info=True,
        )
        return {"messages": messages, "applied": False, "result": None}

    if result is None:
        return {"messages": messages, "applied": False, "result": None}

    return {
        "messages": result["messages"],
        "applied": True,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Context-aware combined entry point
# ---------------------------------------------------------------------------


async def apply_context_aware_time_based_mc(
    messages: list[dict[str, Any]],
    *,
    query_source: Optional[QuerySource] = None,
    prefer_multi_gap: bool = True,
) -> dict[str, Any]:
    """Apply the best available time-based microcompact strategy.

    When ``prefer_multi_gap`` is True (default), tries the multi-gap approach
    first and falls back to the single-gap (last assistant timestamp) approach.
    When False, uses only the single-gap approach.

    Returns a dict with:
        - 'messages': the (possibly modified) message list
        - 'applied': bool indicating whether any time-based MC fired
        - 'strategy': 'multi_gap', 'single_gap', or None
        - 'result': the result dict, or None
    """
    if prefer_multi_gap:
        result = await apply_time_based_microcompact_multi_gap(
            messages, query_source=query_source
        )
        if result["applied"]:
            return {**result, "strategy": "multi_gap"}

    # Fall through to single-gap
    result = await apply_time_based_microcompact(
        messages, query_source=query_source
    )
    return {**result, "strategy": "single_gap" if result["applied"] else None}


# ---------------------------------------------------------------------------
# Utility: validate configuration
# ---------------------------------------------------------------------------


def validate_time_based_mc_config(
    config: Optional[TimeBasedMCConfig] = None,
) -> dict[str, Any]:
    """Validate a TimeBasedMCConfig and return diagnostics.

    Useful for debugging GrowthBook configuration issues.  Returns a dict
    with 'valid' (bool) and 'issues' (list of str).
    """
    cfg = config or get_time_based_mc_config()
    issues: list[str] = []

    if not isinstance(cfg.enabled, bool):
        issues.append(f"enabled should be bool, got {type(cfg.enabled).__name__}")

    if not isinstance(cfg.gap_threshold_minutes, int) or cfg.gap_threshold_minutes < 1:
        issues.append(
            f"gap_threshold_minutes should be a positive int, "
            f"got {cfg.gap_threshold_minutes!r}"
        )

    if not isinstance(cfg.keep_recent, int) or cfg.keep_recent < 1:
        issues.append(
            f"keep_recent should be a positive int, got {cfg.keep_recent!r}"
        )

    return {"valid": len(issues) == 0, "issues": issues, "config": cfg}


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # Config
    "TimeBasedMCConfig",
    "TimeGapInfo",
    "get_time_based_mc_config",
    "validate_time_based_mc_config",
    # Trigger / gate
    "evaluate_time_based_trigger",
    "should_time_based_microcompact",
    "find_gap_cutoff_index",
    # Core application
    "maybe_time_based_microcompact",
    "maybe_time_based_microcompact_multi_gap",
    "apply_time_based_microcompact",
    "apply_time_based_microcompact_multi_gap",
    "apply_context_aware_time_based_mc",
    # Estimation
    "estimate_time_based_savings",
    # Utility (shared with micro_compact.py)
    "_extract_timestamp",
    "_find_time_gaps",
    "_identify_compactable_blocks",
    "_collect_compactable_tool_ids",
    "_collect_compactable_tool_result_ids",
    "_infer_tool_name",
    "_estimate_msg_tokens",
    "_calculate_tool_result_tokens",
    "_calculate_block_tokens",
    "_apply_content_clearing",
    # Constants
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "COMPACTABLE_TOOL_NAMES",
    "IMAGE_MAX_TOKEN_SIZE",
    "MIN_KEEP_RECENT",
    "CACHE_EDITS_MIN_TOKEN_THRESHOLD",
]
