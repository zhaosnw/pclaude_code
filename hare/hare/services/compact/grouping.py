"""
Message grouping for compaction.

Port of: src/services/compact/grouping.ts

Provides message grouping strategies for selective compaction:
- API round grouping (original)
- Tool-turn grouping
- Token-budget grouping
- Recency-based grouping
- Importance-based selective compaction planning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

from hare.services.token_estimation import estimate_tokens


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GroupingStrategy(Enum):
    """Available grouping strategies."""

    API_ROUND = auto()
    TOOL_TURN = auto()
    TOKEN_BUDGET = auto()
    RECENCY = auto()
    HYBRID = auto()  # Combine multiple strategies


class CompactionAction(Enum):
    """What to do with a message group during selective compaction."""

    KEEP = "keep"  # Keep intact, do not compact
    COMPACT = "compact"  # Summarize / compress this group
    DROP = "drop"  # Remove entirely (e.g., tombstoned or trivial)


class GroupImportance(Enum):
    """Importance classification for a message group."""

    CRITICAL = "critical"  # System prompts, tool definitions, etc.
    HIGH = "high"  # Recent user instructions, active tool results
    MEDIUM = "medium"  # Older conversations, reference material
    LOW = "low"  # Duplicate info, truncated tool results
    TRIVIAL = "trivial"  # Tombstones, pure progress messages


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MessageGroup:
    """A group of messages with associated metadata."""

    messages: list[dict[str, Any]]
    start_index: int  # Position in the original message list
    end_index: int  # Exclusive end index
    estimated_tokens: int = 0
    strategy: GroupingStrategy = GroupingStrategy.API_ROUND
    importance: GroupImportance = GroupImportance.MEDIUM
    label: str = ""

    def __post_init__(self) -> None:
        if self.estimated_tokens == 0:
            self.estimated_tokens = _estimate_group_tokens(self.messages)


@dataclass
class SelectiveCompactionPlan:
    """A plan that specifies which message groups to compact and how."""

    groups: list[MessageGroup] = field(default_factory=list)
    actions: dict[int, CompactionAction] = field(default_factory=dict)
    total_tokens_before: int = 0
    estimated_tokens_after: int = 0
    groups_to_compact: list[MessageGroup] = field(default_factory=list)
    groups_to_keep: list[MessageGroup] = field(default_factory=list)
    groups_to_drop: list[MessageGroup] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.total_tokens_before == 0:
            self.total_tokens_before = sum(g.estimated_tokens for g in self.groups)


# ---------------------------------------------------------------------------
# Grouping strategies
# ---------------------------------------------------------------------------


def group_messages_by_api_round(
    messages: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group messages into API request/response rounds.

    Boundary detection uses assistant message IDs (matching the TypeScript
    original): a new round begins when a new assistant message id appears
    that differs from the previous assistant's id. Streaming chunks from
    the same API response share an id, so they stay grouped together.
    Tool results interleaved between chunks of the same response also
    stay in the same group.

    For malformed inputs (dangling tool_use after resume/truncation) the
    caller's ensureToolResultPairing / equivalent should repair the split
    at API time.
    """
    if not messages:
        return []

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    last_assistant_id: Optional[str] = None

    for msg in messages:
        if not isinstance(msg, dict):
            # Skip non-dict items defensively
            continue

        if (
            msg.get("type") == "assistant"
            and msg.get("message", {}).get("id") != last_assistant_id
            and current
        ):
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)

        if msg.get("type") == "assistant":
            last_assistant_id = msg.get("message", {}).get("id")

    if current:
        groups.append(current)

    return groups


def group_messages_by_user_turns(
    messages: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group messages into user-initiated conversation turns.

    A new turn starts with each user message (not a tool_result).
    System messages between turns are attached to the preceding turn.
    This is the original pre-API-round grouping behavior, preserved
    for backwards compatibility.
    """
    if not messages:
        return []

    groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type", "")
        if msg_type == "user" and current_group:
            groups.append(current_group)
            current_group = []
        current_group.append(msg)

    if current_group:
        groups.append(current_group)

    return groups


def group_messages_by_tool_turns(
    messages: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group messages into tool-use turns.

    Each turn consists of: an assistant message with tool_use blocks,
    followed by the corresponding user message(s) with tool_result blocks.
    Messages without tool use/results form their own groups.
    """
    groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    in_tool_turn = False

    for msg in messages:
        msg_type = msg.get("type", "")

        if msg_type == "assistant":
            if _has_tool_use(msg) and not in_tool_turn:
                # Start a new tool turn
                if current_group:
                    groups.append(current_group)
                    current_group = []
                in_tool_turn = True
            elif not _has_tool_use(msg) and in_tool_turn:
                # Plain assistant message ends the tool turn
                groups.append(current_group)
                current_group = []
                in_tool_turn = False

            current_group.append(msg)

        elif msg_type == "user":
            if in_tool_turn:
                if _has_tool_result(msg):
                    # Tool result continues the turn
                    current_group.append(msg)
                else:
                    # Non-tool user message ends the turn
                    groups.append(current_group)
                    current_group = [msg]
                    in_tool_turn = False
            else:
                # Start of a new user-initiated round
                if current_group:
                    groups.append(current_group)
                    current_group = [msg]
                else:
                    current_group.append(msg)

        else:
            # System, progress, attachment messages stay with current group
            current_group.append(msg)

    if current_group:
        groups.append(current_group)

    return groups


def group_messages_by_token_budget(
    messages: list[dict[str, Any]],
    *,
    max_tokens_per_group: int = 10_000,
    strategy: GroupingStrategy = GroupingStrategy.API_ROUND,
) -> list[list[dict[str, Any]]]:
    """Group messages so each group stays under a token budget.

    First groups by the base strategy, then merges or splits groups
    to respect the token budget.
    """
    if strategy == GroupingStrategy.TOOL_TURN:
        base_groups = group_messages_by_tool_turns(messages)
    else:
        base_groups = group_messages_by_api_round(messages)

    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0

    for group in base_groups:
        group_tokens = sum(
            _estimate_message_tokens(msg) for msg in group
        )

        if current_tokens + group_tokens <= max_tokens_per_group:
            current.extend(group)
            current_tokens += group_tokens
        else:
            if current:
                result.append(current)
            # If the group itself exceeds the budget, split it
            if group_tokens > max_tokens_per_group:
                sub_groups = _split_group_by_tokens(group, max_tokens_per_group)
                result.extend(sub_groups)
                current = []
                current_tokens = 0
            else:
                current = list(group)
                current_tokens = group_tokens

    if current:
        result.append(current)

    return result


def group_messages_by_recency(
    messages: list[dict[str, Any]],
    *,
    recent_count: Optional[int] = None,
    recent_ratio: float = 0.3,
    base_strategy: GroupingStrategy = GroupingStrategy.API_ROUND,
) -> tuple[list[list[dict[str, Any]]], list[list[dict[str, Any]]]]:
    """Split messages into older and recent groups.

    Returns (older_groups, recent_groups) where recent_groups are
    the most recent messages that should be kept intact.
    """
    if base_strategy == GroupingStrategy.TOOL_TURN:
        base_groups = group_messages_by_tool_turns(messages)
    else:
        base_groups = group_messages_by_api_round(messages)

    if not base_groups:
        return [], []

    # Determine split point
    if recent_count is not None:
        num_recent = min(recent_count, len(base_groups))
    else:
        num_recent = max(1, int(len(base_groups) * recent_ratio))

    # Keep at least 1 group recent, at least 1 group older
    num_recent = max(1, min(num_recent, len(base_groups) - 1))

    older = base_groups[:-num_recent]
    recent = base_groups[-num_recent:]

    return older, recent


def group_messages_hybrid(
    messages: list[dict[str, Any]],
    *,
    recent_ratio: float = 0.25,
    max_tokens_per_group: int = 12_000,
) -> list[list[dict[str, Any]]]:
    """Hybrid grouping: tool turns for recent messages, API rounds for older,
    with token budget applied to older groups.
    """
    if not messages:
        return []

    older_groups, recent_groups = group_messages_by_recency(
        messages,
        recent_ratio=recent_ratio,
        base_strategy=GroupingStrategy.TOOL_TURN,
    )

    # Recent groups stay as-is (tool-turn granularity)
    # Older groups get merged into token-budgeted API rounds
    older_messages: list[dict[str, Any]] = []
    for g in older_groups:
        older_messages.extend(g)

    budgeted_older = group_messages_by_token_budget(
        older_messages,
        max_tokens_per_group=max_tokens_per_group,
        strategy=GroupingStrategy.API_ROUND,
    )

    return budgeted_older + recent_groups


# ---------------------------------------------------------------------------
# MessageGroup construction helpers
# ---------------------------------------------------------------------------


def build_message_groups(
    messages: list[dict[str, Any]],
    *,
    strategy: GroupingStrategy = GroupingStrategy.API_ROUND,
    max_tokens_per_group: int = 10_000,
    recent_ratio: float = 0.3,
) -> list[MessageGroup]:
    """Build MessageGroup objects from messages using the specified strategy."""
    if strategy == GroupingStrategy.TOOL_TURN:
        raw_groups = group_messages_by_tool_turns(messages)
    elif strategy == GroupingStrategy.TOKEN_BUDGET:
        raw_groups = group_messages_by_token_budget(
            messages, max_tokens_per_group=max_tokens_per_group
        )
    elif strategy == GroupingStrategy.RECENCY:
        older, recent = group_messages_by_recency(
            messages, recent_ratio=recent_ratio
        )
        raw_groups = older + recent
    elif strategy == GroupingStrategy.HYBRID:
        raw_groups = group_messages_hybrid(
            messages,
            recent_ratio=recent_ratio,
            max_tokens_per_group=max_tokens_per_group,
        )
    else:
        raw_groups = group_messages_by_api_round(messages)

    groups: list[MessageGroup] = []
    idx = 0
    for i, raw in enumerate(raw_groups):
        group = MessageGroup(
            messages=raw,
            start_index=idx,
            end_index=idx + len(raw),
            strategy=strategy,
            label=f"group_{i}",
        )
        groups.append(group)
        idx += len(raw)

    return groups


# ---------------------------------------------------------------------------
# Selective compaction: importance classification
# ---------------------------------------------------------------------------


def classify_group_importance(
    group: MessageGroup,
    *,
    group_index: int = 0,
    total_groups: int = 1,
) -> GroupImportance:
    """Classify a message group by importance for selective compaction decisions.

    Rules (in priority order):
      1. System messages with tool definitions or compact boundaries are CRITICAL.
      2. Recent groups (last 25%) are HIGH.
      3. Groups with only tombstones or progress messages are TRIVIAL.
      4. Groups with truncated/micro-compacted content are LOW.
      5. Everything else is MEDIUM.
    """
    # Check for critical system messages
    for msg in group.messages:
        if msg.get("type") == "system":
            subtype = msg.get("subtype", "")
            content = msg.get("content", "")
            if subtype in ("tool_definitions", "initial_prompt") or (
                "system" in subtype.lower()
                and any(
                    kw in content.lower()
                    for kw in ("tool definition", "system prompt", "instructions")
                )
            ):
                return GroupImportance.CRITICAL

    # Recent groups are HIGH importance
    recent_threshold = max(0, total_groups - max(1, total_groups // 4))
    if group_index >= recent_threshold:
        return GroupImportance.HIGH

    # Check for trivial content (only tombstones, progress, or empty groups)
    if _is_trivial_group(group):
        return GroupImportance.TRIVIAL

    # Check for low-value content (micro-compacted results, duplicate info)
    if _is_low_value_group(group):
        return GroupImportance.LOW

    # Check for high-value content (active tool results, user instructions)
    if _has_recent_user_instructions(group):
        return GroupImportance.HIGH

    return GroupImportance.MEDIUM


def classify_all_groups(groups: list[MessageGroup]) -> None:
    """Classify all groups in-place, setting their importance attribute."""
    total = len(groups)
    for i, group in enumerate(groups):
        group.importance = classify_group_importance(
            group, group_index=i, total_groups=total
        )


# ---------------------------------------------------------------------------
# Selective compaction: action selection
# ---------------------------------------------------------------------------


def select_groups_for_compaction(
    groups: list[MessageGroup],
    *,
    target_token_reduction: float = 0.4,
    max_compacted_tokens: int = 50_000,
    min_groups_to_keep: int = 2,
    force_compact_importance: Optional[set[GroupImportance]] = None,
) -> SelectiveCompactionPlan:
    """Build a selective compaction plan.

    Decides which groups to compact, keep, or drop based on importance
    classification and token constraints.

    Args:
        groups: Message groups to evaluate.
        target_token_reduction: Fraction of tokens to try to remove via compaction.
        max_compacted_tokens: Maximum tokens to pass through the compactor.
        min_groups_to_keep: Minimum number of most recent groups to always keep.
        force_compact_importance: Set of importance levels to force-compact
            even if they would normally be kept. Default: {LOW, TRIVIAL}.

    Returns:
        SelectiveCompactionPlan with actions assigned.
    """
    if force_compact_importance is None:
        force_compact_importance = {GroupImportance.LOW, GroupImportance.TRIVIAL}

    # Classify all groups if not already done
    classify_all_groups(groups)

    total_tokens = sum(g.estimated_tokens for g in groups)
    target_tokens = int(total_tokens * target_token_reduction)

    plan = SelectiveCompactionPlan(groups=list(groups))

    compacted_tokens = 0
    kept_groups_count = 0

    # Process groups from oldest to newest
    for i, group in enumerate(groups):
        is_recent = i >= len(groups) - min_groups_to_keep

        if group.importance == GroupImportance.CRITICAL:
            # Never compact critical groups
            action = CompactionAction.KEEP

        elif group.importance == GroupImportance.TRIVIAL:
            # Drop trivial groups
            action = CompactionAction.DROP

        elif group.importance in force_compact_importance:
            # Force-compact low-value groups
            if compacted_tokens < max_compacted_tokens:
                action = CompactionAction.COMPACT
                compacted_tokens += group.estimated_tokens
            else:
                action = CompactionAction.KEEP

        elif is_recent and kept_groups_count < min_groups_to_keep:
            # Keep the most recent groups
            action = CompactionAction.KEEP
            kept_groups_count += 1

        elif compacted_tokens + group.estimated_tokens <= max_compacted_tokens and compacted_tokens < target_tokens:
            # Compact medium/high groups if we haven't hit the budget
            action = CompactionAction.COMPACT
            compacted_tokens += group.estimated_tokens

        else:
            # Keep everything else
            action = CompactionAction.KEEP
            kept_groups_count += 1

        plan.actions[i] = action

    # Populate categorized lists
    for i, group in enumerate(groups):
        action = plan.actions[i]
        if action == CompactionAction.COMPACT:
            plan.groups_to_compact.append(group)
        elif action == CompactionAction.KEEP:
            plan.groups_to_keep.append(group)
        elif action == CompactionAction.DROP:
            plan.groups_to_drop.append(group)

    # Estimate tokens after
    kept_tokens = sum(g.estimated_tokens for g in plan.groups_to_keep)
    # Compacted groups get summarized: roughly 10% of original size
    compacted_result_tokens = int(
        sum(g.estimated_tokens for g in plan.groups_to_compact) * 0.1
    )
    plan.estimated_tokens_after = kept_tokens + compacted_result_tokens

    return plan


def build_selective_compaction_plan(
    messages: list[dict[str, Any]],
    *,
    strategy: GroupingStrategy = GroupingStrategy.HYBRID,
    target_token_reduction: float = 0.4,
    max_compacted_tokens: int = 50_000,
    min_groups_to_keep: int = 2,
    max_tokens_per_group: int = 10_000,
    recent_ratio: float = 0.25,
) -> SelectiveCompactionPlan:
    """High-level entry point: build a complete selective compaction plan.

    Groups messages using the chosen strategy, classifies importance,
    and selects groups for compaction.

    Args:
        messages: Full message list to analyze.
        strategy: Grouping strategy to use.
        target_token_reduction: Fraction of tokens to target for removal.
        max_compacted_tokens: Maximum tokens to send through the compactor.
        min_groups_to_keep: Minimum number of recent groups to keep intact.
        max_tokens_per_group: Token budget per group (for budget/hybrid strategies).
        recent_ratio: Fraction of groups to consider "recent" (for recency/hybrid).

    Returns:
        SelectiveCompactionPlan with full grouping and action assignments.
    """
    groups = build_message_groups(
        messages,
        strategy=strategy,
        max_tokens_per_group=max_tokens_per_group,
        recent_ratio=recent_ratio,
    )

    if not groups:
        return SelectiveCompactionPlan()

    return select_groups_for_compaction(
        groups,
        target_token_reduction=target_token_reduction,
        max_compacted_tokens=max_compacted_tokens,
        min_groups_to_keep=min_groups_to_keep,
    )


# ---------------------------------------------------------------------------
# Merge compacted results with kept groups
# ---------------------------------------------------------------------------


def merge_compacted_plan(
    plan: SelectiveCompactionPlan,
    compacted_summaries: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge compacted group summaries with kept groups back into a message list.

    Args:
        plan: The selective compaction plan.
        compacted_summaries: Dict mapping group index to summary messages
            produced by the compactor for that group.

    Returns:
        Reconstructed message list: kept groups + summary messages for
        compacted groups + recent groups, in original order.
    """
    result: list[dict[str, Any]] = []

    # Pre-compute dropped indices from the actions dict to avoid O(n^2)
    # list.index() calls during iteration.
    dropped_indices: set[int] = {
        i for i, action in plan.actions.items()
        if action == CompactionAction.DROP
    }

    for i, group in enumerate(plan.groups):
        if i in dropped_indices:
            continue

        action = plan.actions.get(i, CompactionAction.KEEP)

        if action == CompactionAction.KEEP:
            result.extend(group.messages)
        elif action == CompactionAction.COMPACT:
            summary = compacted_summaries.get(i, [])
            if summary:
                result.extend(summary)
            else:
                # Fallback: keep original if no summary provided
                result.extend(group.messages)

    return result


def build_summary_message(
    summary_text: str,
    *,
    group_label: str = "",
    compacted_count: int = 0,
) -> dict[str, Any]:
    """Build a system message representing a compaction summary.

    Args:
        summary_text: The summary content.
        group_label: Optional label for the group being summarized.
        compacted_count: Number of original messages summarized.

    Returns:
        A system message dict suitable for insertion into the message list.
    """
    label_prefix = f"[{group_label}] " if group_label else ""
    compact_info = (
        f" (summarized {compacted_count} messages)" if compacted_count > 0 else ""
    )

    content = f"{label_prefix}Previous conversation summary{compact_info}:\n\n{summary_text}"

    return {
        "type": "system",
        "subtype": "compact_boundary",
        "content": content,
        "compact_metadata": {
            "group_label": group_label,
            "compacted_count": compacted_count,
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_message_tokens(msg: dict[str, Any]) -> int:
    """Estimate tokens for a single message dict."""
    msg_type = msg.get("type", "")
    if msg_type not in ("user", "assistant", "system"):
        return 4  # Minimal overhead for progress/attachment/tombstone

    content = msg.get("message", {}).get("content", "") if msg_type in ("user", "assistant") else msg.get("content", "")

    if isinstance(content, str):
        return estimate_tokens(content) + 4

    total = 4  # per-message overhead
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                total += estimate_tokens(block.get("text", ""))
            elif block_type == "tool_use":
                total += estimate_tokens(str(block.get("input", {}))) + 20
            elif block_type == "tool_result":
                rc = block.get("content", "")
                if isinstance(rc, str):
                    total += estimate_tokens(rc)
                elif isinstance(rc, list):
                    for item in rc:
                        if isinstance(item, dict) and item.get("type") == "text":
                            total += estimate_tokens(item.get("text", ""))
            elif block_type == "image":
                total += 1600
            elif block_type == "document":
                total += 1600
            elif block_type == "thinking":
                total += estimate_tokens(block.get("thinking", ""))
    return total


def _estimate_group_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens for a group of messages."""
    return sum(_estimate_message_tokens(m) for m in messages)


def _has_tool_use(msg: dict[str, Any]) -> bool:
    """Check if an assistant message contains tool_use blocks."""
    if msg.get("type") != "assistant":
        return False
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in content
        )
    return False


def _has_tool_result(msg: dict[str, Any]) -> bool:
    """Check if a user message contains tool_result blocks."""
    if msg.get("type") != "user":
        return False
    content = msg.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def _split_group_by_tokens(
    group: list[dict[str, Any]],
    max_tokens: int,
) -> list[list[dict[str, Any]]]:
    """Split a single group into sub-groups respecting the token budget."""
    sub_groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0

    for msg in group:
        msg_tokens = _estimate_message_tokens(msg)
        if current_tokens + msg_tokens > max_tokens and current:
            sub_groups.append(current)
            current = []
            current_tokens = 0
        current.append(msg)
        current_tokens += msg_tokens

    if current:
        sub_groups.append(current)

    return sub_groups if sub_groups else [group]


def _is_trivial_group(group: MessageGroup) -> bool:
    """Check if a group contains only trivial messages (tombstones, progress)."""
    if not group.messages:
        return True

    meaningful = 0
    for msg in group.messages:
        msg_type = msg.get("type", "")
        if msg_type == "tombstone":
            continue
        if msg_type == "progress":
            continue
        if msg_type == "system" and msg.get("subtype", "") == "compact_boundary":
            continue
        meaningful += 1

    return meaningful == 0


def _is_low_value_group(group: MessageGroup) -> bool:
    """Check if a group has low-value content (truncated results, duplicates)."""
    low_signals = 0
    total_content_blocks = 0

    for msg in group.messages:
        content = msg.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                total_content_blocks += 1
                if block.get("type") == "tool_result":
                    block_content = block.get("content", "")
                    if isinstance(block_content, str):
                        if "[... truncated" in block_content:
                            low_signals += 1
                        elif "[Old tool result content cleared]" in block_content:
                            low_signals += 1

    if total_content_blocks == 0:
        return False
    return (low_signals / total_content_blocks) > 0.5


def _has_recent_user_instructions(group: MessageGroup) -> bool:
    """Check if a group contains recent user instructions (non-tool-result).

    A genuine user instruction is a user message that has substantive text
    content that is NOT merely a container for tool results. Messages that
    are purely tool_result carriers, compact summaries, or metadata/logging
    messages are excluded.
    """
    for msg in group.messages:
        if msg.get("type") != "user":
            continue
        if msg.get("is_compact_summary"):
            continue
        if msg.get("is_meta"):
            continue
        if msg.get("subtype") == "compact_boundary":
            continue

        # A user message with text content (not purely tool results) counts
        # as an instruction.
        content = msg.get("message", {}).get("content", "")
        if isinstance(content, str) and content.strip():
            return True

        if isinstance(content, list):
            # Separate text blocks from non-text blocks
            text_blocks = [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            non_text_blocks = [
                b for b in content
                if isinstance(b, dict) and b.get("type") != "text"
            ]

            # If all non-text blocks are tool_results, this message is a
            # tool-result carrier — not a user instruction.
            has_tool_only = (
                len(non_text_blocks) > 0
                and all(
                    b.get("type") == "tool_result"
                    for b in non_text_blocks
                )
            )

            # Check for substantive text blocks (> 20 chars means real text)
            has_substantive_text = any(
                isinstance(b, dict)
                and b.get("type") == "text"
                and len(b.get("text", "").strip()) > 20
                for b in text_blocks
            )

            # A user instruction has both text and tool results (e.g.,
            # "read this file" + file content result), OR text-only with
            # no tool results at all.
            if has_substantive_text:
                if not non_text_blocks:
                    # Pure text message (no tool results) is an instruction
                    return True
                if not has_tool_only:
                    # Mixed content that isn't purely tool results
                    return True
                # If it has substantive text but non-text blocks are all
                # tool_results, it's still likely a user instruction (the
                # tool results are supporting context).
                return True

    return False


def _find_group_index(
    groups: list[MessageGroup], target: MessageGroup
) -> int:
    """Find the index of a MessageGroup in a list via identity then equality.

    Uses id() for fast identity check first, then falls back to structural
    equality. Returns -1 if not found.
    """
    target_id = id(target)
    for i, g in enumerate(groups):
        if id(g) == target_id:
            return i
    # Fallback: structural equality
    for i, g in enumerate(groups):
        if (
            g.start_index == target.start_index
            and g.end_index == target.end_index
            and g.messages == target.messages
        ):
            return i
    return -1


# ---------------------------------------------------------------------------
# Public convenience / statistics / validation
# ---------------------------------------------------------------------------


@dataclass
class GroupStatistics:
    """Aggregate statistics for a set of message groups."""

    total_groups: int = 0
    total_messages: int = 0
    total_tokens: int = 0
    min_tokens_per_group: int = 0
    max_tokens_per_group: int = 0
    avg_tokens_per_group: float = 0.0
    min_messages_per_group: int = 0
    max_messages_per_group: int = 0
    avg_messages_per_group: float = 0.0
    importance_distribution: dict[str, int] = field(default_factory=dict)
    strategy_used: str = ""


def estimate_tokens_for_messages(
    messages: list[dict[str, Any]],
) -> int:
    """Public convenience: estimate total tokens for a list of messages.

    Handles empty lists, non-dict items, and nested content blocks.
    """
    if not messages:
        return 0
    return sum(
        _estimate_message_tokens(m)
        for m in messages
        if isinstance(m, dict)
    )


def flatten_groups(groups: list[MessageGroup]) -> list[dict[str, Any]]:
    """Flatten message groups back into a single ordered message list.

    Messages are concatenated in group order. This is the inverse of
    building groups from messages (modulo grouping strategy).
    """
    result: list[dict[str, Any]] = []
    for group in groups:
        result.extend(group.messages)
    return result


def get_group_statistics(
    groups: list[MessageGroup],
    *,
    strategy_name: str = "",
) -> GroupStatistics:
    """Compute aggregate statistics across a list of message groups.

    Args:
        groups: The message groups to analyze.
        strategy_name: Optional label for the strategy used to produce
            these groups.

    Returns:
        GroupStatistics with computed metrics.
    """
    if not groups:
        return GroupStatistics(strategy_used=strategy_name)

    token_counts = [g.estimated_tokens for g in groups]
    message_counts = [len(g.messages) for g in groups]
    importance_dist: dict[str, int] = {}

    for g in groups:
        key = g.importance.value if hasattr(g.importance, "value") else str(g.importance)
        importance_dist[key] = importance_dist.get(key, 0) + 1

    n = len(groups)
    return GroupStatistics(
        total_groups=n,
        total_messages=sum(message_counts),
        total_tokens=sum(token_counts),
        min_tokens_per_group=min(token_counts) if token_counts else 0,
        max_tokens_per_group=max(token_counts) if token_counts else 0,
        avg_tokens_per_group=sum(token_counts) / n if n > 0 else 0.0,
        min_messages_per_group=min(message_counts) if message_counts else 0,
        max_messages_per_group=max(message_counts) if message_counts else 0,
        avg_messages_per_group=sum(message_counts) / n if n > 0 else 0.0,
        importance_distribution=importance_dist,
        strategy_used=strategy_name,
    )


def validate_group_integrity(
    groups: list[MessageGroup],
    original_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate that groups faithfully represent the original message list.

    Checks:
      - No gaps or overlaps in group index ranges.
      - All original messages are accounted for (no missing, no extras).
      - Group ordering is consistent with start_index ordering.

    Args:
        groups: Message groups to validate.
        original_messages: The original message list the groups were
            built from.

    Returns:
        Dict with keys:
          - valid: bool
          - errors: list of error description strings
          - warnings: list of warning description strings
          - coverage_pct: percentage of original messages covered
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not groups:
        if original_messages:
            errors.append("No groups but original messages exist")
        return {"valid": len(errors) == 0, "errors": errors,
                "warnings": warnings, "coverage_pct": 0.0}

    # Check ordering by start_index
    for i in range(1, len(groups)):
        if groups[i].start_index < groups[i - 1].start_index:
            errors.append(
                f"Groups out of order at index {i}: "
                f"start_index {groups[i].start_index} < "
                f"{groups[i - 1].start_index}"
            )

    # Check for gaps and overlaps
    covered: set[int] = set()
    for i, group in enumerate(groups):
        if group.start_index >= group.end_index:
            errors.append(
                f"Group {i}: start_index ({group.start_index}) >= "
                f"end_index ({group.end_index})"
            )
            continue
        indices = set(range(group.start_index, group.end_index))
        overlap = covered & indices
        if overlap:
            errors.append(
                f"Group {i}: overlapping indices with previous groups: "
                f"{sorted(overlap)[:10]}..."
            )
        covered |= indices

    # Check full coverage
    if original_messages:
        expected = set(range(len(original_messages)))
        missing = expected - covered
        extra = covered - expected
        if missing:
            errors.append(
                f"Missing message indices: {sorted(missing)[:20]}..."
            )
        if extra:
            errors.append(
                f"Extra indices beyond original range: {sorted(extra)[:20]}..."
            )
        coverage_pct = (
            len(covered & expected) / len(expected) * 100.0
            if expected
            else 100.0
        )
    else:
        coverage_pct = 100.0

    # Check group message counts match the range
    for i, group in enumerate(groups):
        expected_len = group.end_index - group.start_index
        actual_len = len(group.messages)
        if actual_len != expected_len:
            warnings.append(
                f"Group {i}: message count {actual_len} != "
                f"range size {expected_len} (indices "
                f"{group.start_index}-{group.end_index})"
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "coverage_pct": round(coverage_pct, 1),
    }


def compute_compaction_savings(
    plan: SelectiveCompactionPlan,
) -> dict[str, Any]:
    """Compute detailed compaction savings estimates from a plan.

    Returns a dict with token-level and message-level savings breakdowns.
    """
    if not plan.groups:
        return {
            "tokens_before": 0,
            "tokens_after": 0,
            "tokens_saved": 0,
            "savings_pct": 0.0,
            "messages_before": 0,
            "messages_after": 0,
            "messages_removed": 0,
            "groups_kept": 0,
            "groups_compacted": 0,
            "groups_dropped": 0,
        }

    tokens_before = plan.total_tokens_before
    tokens_after = plan.estimated_tokens_after

    messages_before = sum(len(g.messages) for g in plan.groups)
    messages_kept = sum(len(g.messages) for g in plan.groups_to_keep)
    # Compacted groups are summarized to roughly 1 message each
    messages_compacted = len(plan.groups_to_compact)
    messages_after = messages_kept + messages_compacted

    return {
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "tokens_saved": max(0, tokens_before - tokens_after),
        "savings_pct": (
            round((tokens_before - tokens_after) / tokens_before * 100.0, 1)
            if tokens_before > 0
            else 0.0
        ),
        "messages_before": messages_before,
        "messages_after": messages_after,
        "messages_removed": messages_before - messages_after,
        "groups_kept": len(plan.groups_to_keep),
        "groups_compacted": len(plan.groups_to_compact),
        "groups_dropped": len(plan.groups_to_drop),
    }


def find_optimal_grouping_strategy(
    messages: list[dict[str, Any]],
    *,
    total_tokens: Optional[int] = None,
) -> tuple[GroupingStrategy, str]:
    """Auto-select the best grouping strategy for a conversation.

    Heuristics (evaluated in order):
      1. Empty / very short (< 500 tokens) -> API_ROUND (no compaction needed).
      2. Many tool-use turns (> 30% of assistant msgs with tool_use)
         -> TOOL_TURN keeps tool chains intact.
      3. Very long (> 50k tokens) -> HYBRID balances recency + budget.
      4. Moderate length (> 15k tokens) and older -> TOKEN_BUDGET for
         uniform sizing.
      5. Default -> API_ROUND.

    Args:
        messages: The message list to evaluate.
        total_tokens: Optional pre-computed token count. If None, it is
            estimated.

    Returns:
        Tuple of (recommended_strategy, reason_string).
    """
    if not messages:
        return GroupingStrategy.API_ROUND, "empty conversation"

    if total_tokens is None:
        total_tokens = estimate_tokens_for_messages(messages)

    # 1. Very short — no grouping complexity needed
    if total_tokens < 500:
        return GroupingStrategy.API_ROUND, (
            f"short conversation ({total_tokens} tokens)"
        )

    # 2. Count tool-use density
    assistant_count = 0
    tool_use_count = 0
    user_message_count = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "assistant":
            assistant_count += 1
            if _has_tool_use(msg):
                tool_use_count += 1
        elif msg.get("type") == "user":
            user_message_count += 1

    tool_use_ratio = (
        tool_use_count / assistant_count if assistant_count > 0 else 0.0
    )

    # 3. Highly tool-heavy conversations
    if tool_use_ratio > 0.3 and assistant_count >= 5:
        return GroupingStrategy.TOOL_TURN, (
            f"tool-heavy conversation "
            f"(tool_use ratio {tool_use_ratio:.2f}, "
            f"{assistant_count} assistant messages)"
        )

    # 4. Very long conversations — hybrid
    if total_tokens > 50_000:
        return GroupingStrategy.HYBRID, (
            f"very long conversation ({total_tokens} tokens)"
        )

    # 5. Moderate length — token budget
    if total_tokens > 15_000:
        return GroupingStrategy.TOKEN_BUDGET, (
            f"moderate length ({total_tokens} tokens)"
        )

    # 6. Single user turn with many tool calls — keep tool turns intact
    if user_message_count <= 2 and tool_use_count > 3:
        return GroupingStrategy.TOOL_TURN, (
            f"single-turn agentic ({user_message_count} user msgs, "
            f"{tool_use_count} tool uses)"
        )

    # 7. Default
    return GroupingStrategy.API_ROUND, (
        f"standard conversation ({total_tokens} tokens)"
    )


def group_messages_by_semantic_boundaries(
    messages: list[dict[str, Any]],
    *,
    min_group_size: int = 2,
    boundary_token_threshold: int = 500,
) -> list[list[dict[str, Any]]]:
    """Group messages by semantic boundaries in the conversation.

    Detects natural break points: long assistant responses, system message
    insertions, and compact boundaries. This is a heuristic grouping that
    tries to keep coherent conversation segments together.

    Args:
        messages: Message list to group.
        min_group_size: Minimum number of messages per semantic group.
        boundary_token_threshold: Tokens above which a single assistant
            message is treated as a boundary.

    Returns:
        List of message groups.
    """
    if not messages:
        return []

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")

        # Boundary signals that start a new group (if current is non-empty):
        # - System messages with compact_boundary subtype
        # - Assistant messages that are very long (likely standalone responses)
        # - System messages with tool_definitions or initial_prompt
        is_boundary = False

        if msg_type == "system":
            subtype = msg.get("subtype", "")
            if subtype in ("compact_boundary", "tool_definitions", "initial_prompt"):
                is_boundary = True
        elif msg_type == "assistant":
            # A very long assistant response suggests a semantic boundary
            msg_tokens = _estimate_message_tokens(msg)
            if msg_tokens > boundary_token_threshold:
                is_boundary = True

        if is_boundary and len(current) >= min_group_size:
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        groups.append(current)

    # If we produced too many tiny groups, merge adjacent small ones
    if len(groups) > 1:
        merged: list[list[dict[str, Any]]] = []
        buffer: list[dict[str, Any]] = []

        for group in groups:
            if len(group) < min_group_size:
                buffer.extend(group)
            else:
                if buffer:
                    if merged:
                        # Attach buffer to the previous group
                        merged[-1].extend(buffer)
                    else:
                        merged.append(buffer)
                    buffer = []
                merged.append(group)

        if buffer:
            if merged:
                merged[-1].extend(buffer)
            else:
                merged.append(buffer)

        return merged

    return groups


# ---------------------------------------------------------------------------
# Convenience: apply selective compaction end-to-end
# ---------------------------------------------------------------------------


async def apply_selective_compaction(
    messages: list[dict[str, Any]],
    *,
    compact_fn: Optional[
        Callable[
            [list[dict[str, Any]]], Any
        ]  # returns an object with .new_messages or similar
    ] = None,
    strategy: GroupingStrategy = GroupingStrategy.HYBRID,
    target_token_reduction: float = 0.4,
    max_compacted_tokens: int = 50_000,
    min_groups_to_keep: int = 2,
    recent_ratio: float = 0.25,
) -> dict[str, Any]:
    """Apply selective compaction end-to-end.

    Groups messages, selects which groups to compact, runs the compactor
    on each selected group, and merges results.

    Args:
        messages: Full message list.
        compact_fn: Async function that compacts a list of messages and returns
            a result with `.new_messages` attribute. If None, a simple local
            summarizer is used.
        strategy: Grouping strategy.
        target_token_reduction: Target fraction of tokens to reduce.
        max_compacted_tokens: Max tokens to send through compactor.
        min_groups_to_keep: Min recent groups to keep intact.
        recent_ratio: Fraction of groups to consider recent.

    Returns:
        Dict with keys:
            - messages: The compacted message list
            - tokens_before: Original token estimate
            - tokens_after: Post-compaction token estimate
            - tokens_saved: Tokens saved
            - groups_compacted: Number of groups compacted
            - groups_dropped: Number of groups dropped
            - plan: The SelectiveCompactionPlan used
    """
    from hare.services.compact.compact_full import create_simple_summary_for_group

    plan = build_selective_compaction_plan(
        messages,
        strategy=strategy,
        target_token_reduction=target_token_reduction,
        max_compacted_tokens=max_compacted_tokens,
        min_groups_to_keep=min_groups_to_keep,
        recent_ratio=recent_ratio,
    )

    if not plan.groups:
        return {
            "messages": messages,
            "tokens_before": 0,
            "tokens_after": 0,
            "tokens_saved": 0,
            "groups_compacted": 0,
            "groups_dropped": 0,
            "plan": plan,
        }

    # Pre-compute group index map once to avoid O(n^2) index() calls
    group_index_map: dict[int, int] = {
        id(group): i for i, group in enumerate(plan.groups)
    }

    # Compact each selected group
    compacted_summaries: dict[int, list[dict[str, Any]]] = {}

    for group in plan.groups_to_compact:
        group_idx = group_index_map.get(id(group))
        if group_idx is None:
            group_idx = _find_group_index(plan.groups, group)
        if compact_fn is not None:
            try:
                result = await compact_fn(group.messages)
                if hasattr(result, "new_messages"):
                    compacted_summaries[group_idx] = result.new_messages
                elif isinstance(result, dict) and "new_messages" in result:
                    compacted_summaries[group_idx] = result["new_messages"]
                else:
                    compacted_summaries[group_idx] = [result]
            except Exception:
                # Fallback to simple summary on compaction failure
                compacted_summaries[group_idx] = [
                    create_simple_summary_for_group(group)
                ]
        else:
            compacted_summaries[group_idx] = [
                create_simple_summary_for_group(group)
            ]

    merged = merge_compacted_plan(plan, compacted_summaries)

    return {
        "messages": merged,
        "tokens_before": plan.total_tokens_before,
        "tokens_after": plan.estimated_tokens_after,
        "tokens_saved": plan.total_tokens_before - plan.estimated_tokens_after,
        "groups_compacted": len(plan.groups_to_compact),
        "groups_dropped": len(plan.groups_to_drop),
        "plan": plan,
    }
