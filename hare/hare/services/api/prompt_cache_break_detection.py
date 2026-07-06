"""
Prompt cache break detection — identifies when prompt caching is invalidated.

Port of: src/services/api/promptCacheBreakDetection.ts

Detects changes in system prompts, tools, model, or messages that would
break Anthropic's prompt cache, enabling proactive cache management.

Anthropic caches prompts as a prefix: the system prompt, tools, and leading
messages form a cacheable prefix. Compaction strategies that remove early
messages OR alter the system/tools/model break the cache. This module
provides compaction-aware detection: before compacting, call
detect_compaction_cache_impact() to learn whether the proposed action
will invalidate the cache and which boundary is safe.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence


# ---------------------------------------------------------------------------
# Cache break reason constants
# ---------------------------------------------------------------------------

CACHE_BREAK_SYSTEM_PROMPT = "system_prompt_changed"
CACHE_BREAK_TOOLS = "tools_changed"
CACHE_BREAK_MODEL = "model_changed"
CACHE_BREAK_MESSAGES = "messages_changed"
CACHE_BREAK_COMPACTION = "compaction"
CACHE_BREAK_THINKING = "thinking_config_changed"
CACHE_BREAK_TRUNCATION = "prefix_truncated"


# ---------------------------------------------------------------------------
# Compaction pattern enum — classifies the compaction strategy
# ---------------------------------------------------------------------------

class CompactionPattern(Enum):
    """Classifies how compaction alters the message list.

    KEEP_HEAD     — oldest messages preserved, middle/end trimmed  (cache-safe-ish)
    DROP_HEAD     — oldest messages removed, recent kept           (cache-breaking)
    SUMMARIZE_ALL — all messages replaced with summary             (cache-breaking)
    TRIM_TAIL     — remove oldest tool results, keep conversation  (cache-safe)
    UNKNOWN       — cannot determine pattern
    """

    KEEP_HEAD = "keep_head"
    DROP_HEAD = "drop_head"
    SUMMARIZE_ALL = "summarize_all"
    TRIM_TAIL = "trim_tail"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CacheBreakEvent:
    """A single cache invalidation event with full context."""

    reason: str
    break_number: int
    timestamp: float = field(default_factory=time.time)
    detail: str = ""
    was_compaction: bool = False
    prefix_boundary_before: int = -1  # message index where cache prefix ended
    prefix_boundary_after: int = -1


@dataclass(frozen=True)
class CachePrefixSnapshot:
    """Snapshot of what is currently cached by Anthropic.

    Anthropic caches prompts as a prefix. The system prompt, tools
    definition, model, and the first *cached_prefix_message_count*
    messages form the cacheable prefix.  Messages beyond that boundary
    are outside the cache and can be freely altered.
    """

    system_prompt_hash: str = ""
    tools_hash: str = ""
    model: str = ""
    thinking_config_hash: str = ""
    cached_prefix_message_count: int = 0
    total_message_count: int = 0
    captured_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CompactionImpact:
    """Result of analysing how a proposed compaction affects the cache."""

    will_break_cache: bool
    pattern: CompactionPattern
    breaks: list[str]  # which CACHE_BREAK_* reasons are triggered
    safe_prefix_boundary: int  # messages *before* this index are cached
    messages_to_remove: int    # how many messages the compaction removes
    recommended_action: str    # human-readable guidance
    detail: str = ""


# ---------------------------------------------------------------------------
# CacheBreakDetector
# ---------------------------------------------------------------------------

class CacheBreakDetector:
    """Detects changes that would invalidate the Anthropic prompt cache.

    Tracks hashes of cache-sensitive inputs (system prompt, tools, model)
    and detects when any of them change between API calls.

    Also maintains a prefix snapshot so compaction code can query the
    current cache boundary before deciding which messages to evict.
    """

    def __init__(self) -> None:
        self._system_prompt_hash: str = ""
        self._tools_hash: str = ""
        self._model: str = ""
        self._thinking_config_hash: str = ""
        self._last_message_count: int = 0
        self._break_count: int = 0
        self._breaks: list[CacheBreakEvent] = []
        self._prefix_snapshot: Optional[CachePrefixSnapshot] = None

    # -- check methods --------------------------------------------------------

    def check_system_prompt(self, system_prompt: list[str] | str) -> Optional[str]:
        """Check if the system prompt changed since last call."""
        if isinstance(system_prompt, list):
            content = "\n".join(system_prompt)
        else:
            content = str(system_prompt)
        new_hash = hashlib.sha256(content.encode()).hexdigest()
        if self._system_prompt_hash and new_hash != self._system_prompt_hash:
            self._record_break(CACHE_BREAK_SYSTEM_PROMPT,
                               detail=f"hash {self._system_prompt_hash[:8]} -> {new_hash[:8]}")
            self._system_prompt_hash = new_hash
            return CACHE_BREAK_SYSTEM_PROMPT
        self._system_prompt_hash = new_hash
        return None

    def check_tools(self, tools: list[dict[str, Any]]) -> Optional[str]:
        """Check if the tools definition changed since last call."""
        content = _hashable_tools(tools)
        new_hash = hashlib.sha256(content.encode()).hexdigest()
        if self._tools_hash and new_hash != self._tools_hash:
            self._record_break(CACHE_BREAK_TOOLS,
                               detail=f"tool-count {len(tools)}")
            self._tools_hash = new_hash
            return CACHE_BREAK_TOOLS
        self._tools_hash = new_hash
        return None

    def check_model(self, model: str) -> Optional[str]:
        """Check if the model changed since last call."""
        if self._model and model != self._model:
            self._record_break(CACHE_BREAK_MODEL,
                               detail=f"{self._model} -> {model}")
            self._model = model
            return CACHE_BREAK_MODEL
        self._model = model
        return None

    def check_thinking_config(self, thinking_config: Optional[dict[str, Any]]) -> Optional[str]:
        """Check if the thinking configuration changed."""
        if thinking_config is None:
            new_hash = "none"
        else:
            new_hash = hashlib.sha256(
                str(sorted(thinking_config.items())).encode()
            ).hexdigest()
        if self._thinking_config_hash and new_hash != self._thinking_config_hash:
            self._record_break(CACHE_BREAK_THINKING, detail="thinking config changed")
            self._thinking_config_hash = new_hash
            return CACHE_BREAK_THINKING
        self._thinking_config_hash = new_hash
        return None

    def check_message_count(self, message_count: int) -> Optional[str]:
        """Check if message count decreased (compaction removes messages)."""
        if self._last_message_count and message_count < self._last_message_count:
            self._record_break(
                CACHE_BREAK_MESSAGES,
                detail=f"{self._last_message_count} -> {message_count} messages",
            )
            self._last_message_count = message_count
            return CACHE_BREAK_MESSAGES
        self._last_message_count = message_count
        return None

    def check_all(
        self,
        system_prompt: list[str] | str,
        tools: list[dict[str, Any]],
        model: str,
        thinking_config: Optional[dict[str, Any]],
        message_count: int,
    ) -> list[str]:
        """Run every cache-break check in one call.

        Returns the list of break reasons triggered (empty if cache is intact).
        """
        breaks: list[str] = []
        for check in (
            lambda: self.check_system_prompt(system_prompt),
            lambda: self.check_tools(tools),
            lambda: self.check_model(model),
            lambda: self.check_thinking_config(thinking_config),
            lambda: self.check_message_count(message_count),
        ):
            result = check()
            if result:
                breaks.append(result)
        return breaks

    # -- compaction -----------------------------------------------------------

    def mark_compaction(self) -> None:
        """Record a compaction as a cache break event."""
        self._record_break(CACHE_BREAK_COMPACTION,
                           was_compaction=True,
                           detail="compaction event")

    def snapshot_prefix(self, message_count: int) -> CachePrefixSnapshot:
        """Capture a snapshot of the current cacheable prefix state."""
        snap = CachePrefixSnapshot(
            system_prompt_hash=self._system_prompt_hash,
            tools_hash=self._tools_hash,
            model=self._model,
            thinking_config_hash=self._thinking_config_hash,
            cached_prefix_message_count=message_count,
            total_message_count=self._last_message_count,
        )
        self._prefix_snapshot = snap
        return snap

    def get_prefix_snapshot(self) -> Optional[CachePrefixSnapshot]:
        """Return the most recent prefix snapshot, if any."""
        return self._prefix_snapshot

    # -- reporting ------------------------------------------------------------

    def _record_break(
        self,
        reason: str,
        detail: str = "",
        was_compaction: bool = False,
    ) -> None:
        self._break_count += 1
        event = CacheBreakEvent(
            reason=reason,
            break_number=self._break_count,
            detail=detail,
            was_compaction=was_compaction,
        )
        self._breaks.append(event)
        # Keep only last 50 events
        if len(self._breaks) > 50:
            self._breaks = self._breaks[-50:]

    def get_break_count(self) -> int:
        return self._break_count

    def get_recent_breaks(self, limit: int = 10) -> list[CacheBreakEvent]:
        return self._breaks[-limit:]

    def get_breaks_since(self, break_number: int) -> list[CacheBreakEvent]:
        """Return breaks with break_number > the given value."""
        return [b for b in self._breaks if b.break_number > break_number]

    def is_cache_hot(self) -> bool:
        """True if we have recorded state for at least one prior request."""
        return bool(self._system_prompt_hash)

    def reset(self) -> None:
        """Reset all tracking state."""
        self._system_prompt_hash = ""
        self._tools_hash = ""
        self._model = ""
        self._thinking_config_hash = ""
        self._last_message_count = 0
        self._break_count = 0
        self._breaks.clear()
        self._prefix_snapshot = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hashable_tools(tools: list[dict[str, Any]]) -> str:
    """Convert tools list to a hashable string representation."""
    parts = []
    for t in tools:
        if isinstance(t, dict):
            name = t.get("name", t.get("function", {}).get("name", ""))
            desc = t.get("description", t.get("function", {}).get("description", ""))
            parts.append(f"{name}:{desc}")
        elif hasattr(t, "name"):
            parts.append(f"{t.name}:{getattr(t, 'description', '')}")
    return "|".join(sorted(parts))


def _hash_message(message: dict[str, Any]) -> str:
    """Hash a single message for prefix comparison.

    Only hashes the *structure* relevant to cache prefix matching:
    role + content text (not tool_use ids). This is deliberately
    lightweight — Anthropic's internal prefix-matching is more nuanced
    but role+content stability is the dominant signal.
    """
    role = message.get("role", "")
    content = message.get("content", "")
    if isinstance(content, list):
        # content blocks — extract text portions
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    text_parts.append(
                        f"tool_use:{block.get('name', '')}"
                    )
                elif block.get("type") == "tool_result":
                    text_parts.append(
                        f"tool_result:{block.get('tool_use_id', '')}"
                    )
        content_flat = "|".join(text_parts)
    else:
        content_flat = str(content)
    return hashlib.sha256(f"{role}:{content_flat}".encode()).hexdigest()


def _classify_compaction_pattern(
    old_messages: Sequence[dict[str, Any]],
    new_messages: Sequence[dict[str, Any]],
) -> CompactionPattern:
    """Compare old and new message lists to classify the compaction pattern.

    Compares first/last message hashes to determine whether the head
    (prefix) was preserved or dropped.
    """
    if not old_messages or not new_messages:
        return CompactionPattern.UNKNOWN

    if len(new_messages) >= len(old_messages):
        # No messages removed — not a compaction
        return CompactionPattern.UNKNOWN

    old_head_hash = _hash_message(old_messages[0])
    new_head_hash = _hash_message(new_messages[0])

    if old_head_hash == new_head_hash:
        # First message preserved → KEEP_HEAD or TRIM_TAIL
        if len(old_messages) - len(new_messages) <= 2:
            return CompactionPattern.TRIM_TAIL
        return CompactionPattern.KEEP_HEAD

    # First message changed — check if it matches a later old message
    for i, old_msg in enumerate(old_messages):
        if _hash_message(old_msg) == new_head_hash and i > 0:
            return CompactionPattern.DROP_HEAD

    return CompactionPattern.SUMMARIZE_ALL


# ---------------------------------------------------------------------------
# Compaction-aware cache impact detection
# ---------------------------------------------------------------------------

async def detect_compaction_cache_impact(
    current_messages: Sequence[dict[str, Any]],
    proposed_message_count: int,
    system_prompt: list[str] | str = "",
    tools: list[dict[str, Any]] | None = None,
    detector: CacheBreakDetector | None = None,
) -> CompactionImpact:
    """Analyse how a proposed compaction would affect the prompt cache.

    Given the current message list and the target message count after
    compaction, determines:
    - whether the Anthropic cache prefix would be broken
    - which compaction pattern is being used
    - a recommended safe boundary index

    Anthropic caches prompts as a prefix, so removing early messages
    (DROP_HEAD) is the most damaging pattern.  TRIM_TAIL (removing
    only the oldest tool results) is usually safe.

    Args:
        current_messages: The full message list before compaction.
        proposed_message_count: How many messages will remain after.
        system_prompt: Current system prompt (for hash tracking).
        tools: Current tool definitions (for hash tracking).
        detector: Optional existing detector; a fresh one is used if None.

    Returns:
        CompactionImpact with break prediction and guidance.
    """
    det = detector or get_cache_break_detector()
    breaks: list[str] = []

    # Check system prompt stability
    if system_prompt:
        sp_break = det.check_system_prompt(system_prompt)
        if sp_break:
            breaks.append(sp_break)

    # Check tools stability
    if tools:
        tools_break = det.check_tools(tools)
        if tools_break:
            breaks.append(tools_break)

    # Core: does the proposed compaction shrink below the cached prefix?
    snap = det.get_prefix_snapshot()
    safe_boundary = 0

    if snap and snap.cached_prefix_message_count > 0:
        safe_boundary = snap.cached_prefix_message_count
        if proposed_message_count < snap.cached_prefix_message_count:
            breaks.append(CACHE_BREAK_TRUNCATION)
    else:
        # No prior snapshot — use a heuristic: assume the first
        # max(1, floor(len/2)) messages are the cache prefix.
        safe_boundary = max(1, len(current_messages) // 2)

    # Determine how many messages will be removed
    current_count = len(current_messages)
    removed_count = max(0, current_count - proposed_message_count)

    # Classify the pattern by simulating the proposed result
    if removed_count > 0:
        # Simulate: if we keep the *last* N messages (DROP_HEAD style)
        simulated_new = current_messages[removed_count:]
        pattern = _classify_compaction_pattern(current_messages, simulated_new)
    else:
        pattern = CompactionPattern.UNKNOWN

    will_break = len(breaks) > 0 or pattern in (
        CompactionPattern.DROP_HEAD,
        CompactionPattern.SUMMARIZE_ALL,
    )

    if pattern == CompactionPattern.DROP_HEAD:
        recommended = (
            f"DROP_HEAD will break the cache prefix. Prefer TRIM_TAIL: "
            f"remove messages after index {safe_boundary} instead."
        )
    elif pattern == CompactionPattern.SUMMARIZE_ALL:
        recommended = (
            "SUMMARIZE_ALL replaces every message; cache will fully reset. "
            "Consider partial compaction that preserves the first "
            f"{safe_boundary} messages."
        )
    elif will_break:
        recommended = f"Cache break expected: {', '.join(breaks)}. Safe boundary at index {safe_boundary}."
    else:
        recommended = (
            f"Cache-safe compaction possible: keep first {safe_boundary} "
            f"messages, trim tail. {removed_count} messages can be removed "
            f"without breaking the cache."
        )

    # Record the compaction event if messages are actually being removed
    if removed_count > 0:
        det.mark_compaction()

    return CompactionImpact(
        will_break_cache=will_break,
        pattern=pattern,
        breaks=breaks,
        safe_prefix_boundary=safe_boundary,
        messages_to_remove=removed_count,
        recommended_action=recommended,
        detail=(
            f"messages: {current_count} -> {proposed_message_count} "
            f"(remove {removed_count}), pattern={pattern.value}, "
            f"safe_boundary={safe_boundary}"
        ),
    )


async def verify_cache_integrity(
    system_prompt: list[str] | str,
    tools: list[dict[str, Any]],
    model: str,
    thinking_config: Optional[dict[str, Any]],
    message_count: int,
    detector: CacheBreakDetector | None = None,
) -> dict[str, Any]:
    """Pre-flight check: verify all cache inputs before an API call.

    Runs every check (system, tools, model, thinking, message count)
    and returns a dict with 'cache_intact' (bool) and 'breaks' (list[str]).

    Callers should log a warning when cache_intact is False so that
    observability systems can track cache-break frequency.
    """
    det = detector or get_cache_break_detector()
    breaks = det.check_all(
        system_prompt=system_prompt,
        tools=tools,
        model=model,
        thinking_config=thinking_config,
        message_count=message_count,
    )

    # After passing checks, snapshot the current prefix so future
    # compaction decisions know where the boundary is.
    if not breaks:
        det.snapshot_prefix(message_count)

    return {
        "cache_intact": len(breaks) == 0,
        "breaks": breaks,
        "break_count_total": det.get_break_count(),
        "is_first_request": not det.is_cache_hot(),
    }


# ---------------------------------------------------------------------------
# Global detector instance
# ---------------------------------------------------------------------------

_detector: Optional[CacheBreakDetector] = None


def get_cache_break_detector() -> CacheBreakDetector:
    """Get the global cache break detector."""
    global _detector
    if _detector is None:
        _detector = CacheBreakDetector()
    return _detector


async def notify_compaction(
    reason: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Notify the cache break detector of a compaction event.

    Returns a summary dict suitable for logging / telemetry.
    """
    detector = get_cache_break_detector()
    detector.mark_compaction()

    info: dict[str, Any] = {
        "event": "compaction_notified",
        "reason": reason,
        "total_breaks": detector.get_break_count(),
    }
    if meta:
        info["meta"] = meta
    return info
