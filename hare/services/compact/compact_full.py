"""
Full compact system.

Port of: src/services/compact/compact.ts

Implements conversation compaction by summarizing older messages
to free up context window space.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from hare.services.token_estimation import estimate_tokens


POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000
MAX_COMPACT_STREAMING_RETRIES = 2

ERROR_MESSAGE_NOT_ENOUGH_MESSAGES = "Not enough messages to compact"
ERROR_MESSAGE_INCOMPLETE_RESPONSE = "Incomplete response during compaction"
ERROR_MESSAGE_USER_ABORT = "User aborted compaction"


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    # Message objects (UserMessage/AssistantMessage) in the query loop; dicts
    # in offline callers.
    new_messages: list[Any] = field(default_factory=list)
    summary: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    messages_removed: int = 0
    user_display_message: str = ""


def strip_images_from_messages(messages: list[Any]) -> list[Any]:
    """Strip image blocks from messages before compaction.

    The query loop holds UserMessage/AssistantMessage objects, not dicts. This
    assumed dicts and raised AttributeError on the first message, which
    auto_compact_if_needed swallowed into a failure counter — so auto-compact
    never actually ran. Objects are passed through untouched (they carry no
    image blocks in the headless path); dict messages are still rewritten.
    """
    result = []
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue
        if msg.get("type") != "user":
            result.append(msg)
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                new_content.append({"type": "text", "text": "[image]"})
            elif isinstance(block, dict) and block.get("type") == "document":
                new_content.append({"type": "text", "text": "[document]"})
            else:
                new_content.append(block)
        new_msg = {**msg, "message": {**msg.get("message", {}), "content": new_content}}
        result.append(new_msg)
    return result


def should_compact(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 200_000,
    current_tokens: int = 0,
) -> bool:
    """Determine if messages should be compacted."""
    if current_tokens > 0:
        return current_tokens > max_tokens * 0.9
    total = sum(estimate_tokens(str(m)) for m in messages)
    return total > max_tokens * 0.9


def find_compaction_point(
    messages: list[dict[str, Any]],
    *,
    target_ratio: float = 0.5,
) -> int:
    """Find the optimal point to split messages for compaction."""
    if len(messages) <= 2:
        return 0
    target_idx = int(len(messages) * target_ratio)
    # Ensure we keep at least 2 messages
    return max(1, min(target_idx, len(messages) - 2))


def _message_text(msg: Any) -> tuple[str, str]:
    """Return ``(type, text)`` for a message that may be a dict or an object."""
    if isinstance(msg, dict):
        msg_type = msg.get("type", "")
        inner: Any = msg.get("message", {})
    else:
        msg_type = getattr(msg, "type", "")
        inner = getattr(msg, "message", None)
    content = (
        inner.get("content", "")
        if isinstance(inner, dict)
        else getattr(inner, "content", "")
    )
    if isinstance(content, str):
        return msg_type, content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return msg_type, "\n".join(p for p in parts if p)
    return msg_type, ""


def _local_summary(old_messages: list[Any]) -> str:
    """Offline fallback used when no model is available (unit tests)."""
    parts = []
    for msg in old_messages:
        msg_type, text = _message_text(msg)
        if text:
            parts.append(f"[{msg_type}] {text[:200]}")
    return "Previous conversation summary:\n" + "\n".join(parts[:20])


async def _summarize_via_model(
    call_model: Any,
    old_messages: list[Any],
    custom_instructions: Optional[str],
) -> str:
    """Ask the model to summarize the conversation being compacted.

    The reference sends one summarization request (compact/prompt.ts) and uses
    the reply as the summary; hare only ever built a local string, so compaction
    consumed no model turn and produced a different conversation than the
    reference's from that point on.
    """
    from hare.services.compact.prompt import get_compact_prompt

    transcript = "\n\n".join(
        f"[{msg_type}] {text}"
        for msg_type, text in (_message_text(m) for m in old_messages)
        if text
    )
    instruction = get_compact_prompt(custom_instructions=custom_instructions or "")
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{transcript}\n\n{instruction}"}
                ],
            }
        ],
        "system_prompt": [],
        "tools": [],
        "options": {},
    }

    chunks: list[str] = []
    result = call_model(payload)
    if hasattr(result, "__aiter__"):
        async for item in result:
            _, text = _message_text(item)
            if text:
                chunks.append(text)
    else:
        item = await result
        _, text = _message_text(item)
        if text:
            chunks.append(text)
    summary = "\n".join(chunks).strip()
    return summary or _local_summary(old_messages)


async def compact_messages(
    messages: list[Any],
    *,
    custom_instructions: Optional[str] = None,
    max_tokens: int = 200_000,
    call_model: Any = None,
) -> CompactionResult:
    """
    Compact a list of messages by summarizing older ones.

    With ``call_model`` the summary comes from the model, as in the reference.
    Without it (unit tests, offline callers) a local summary is used.
    """
    if len(messages) < 3:
        raise ValueError(ERROR_MESSAGE_NOT_ENOUGH_MESSAGES)

    stripped = strip_images_from_messages(messages)
    split_point = find_compaction_point(stripped)

    old_messages = stripped[:split_point]
    kept_messages = stripped[split_point:]

    if call_model is not None:
        summary = await _summarize_via_model(
            call_model, old_messages, custom_instructions
        )
    else:
        summary = _local_summary(old_messages)

    # The compacted conversation is replayed to the model, so the summary has to
    # be a real message object like the rest of the loop's messages — a bare
    # dict blew up downstream ("'dict' object has no attribute 'type'").
    from hare.utils.messages import create_user_message

    summary_message = create_user_message(
        content=[{"type": "text", "text": summary}],
    )

    # The compacted conversation IS the summary: query.ts replaces
    # messagesForQuery with the post-compact messages, and the reference's next
    # request carries exactly one message. Keeping the tail here both
    # double-counted turns (every kept message is yielded again) and left the
    # context it was supposed to free.
    new_messages = [summary_message]

    return CompactionResult(
        new_messages=new_messages,
        summary=summary,
        tokens_before=sum(estimate_tokens(str(m)) for m in messages),
        tokens_after=sum(estimate_tokens(str(m)) for m in new_messages),
        messages_removed=len(old_messages),
    )


async def compact_conversation(
    messages: list[Any],
    context: Any = None,
    cache_params: Any = None,
    is_auto: bool = False,
    custom_instructions: str = "",
    is_partial: bool = False,
    call_model: Any = None,
) -> CompactionResult:
    """Full conversation compaction (model-generated summary when available)."""
    return await compact_messages(
        messages,
        custom_instructions=custom_instructions or None,
        call_model=call_model,
    )


def merge_hook_instructions(
    custom_instructions: str,
    hook_instructions: Optional[str],
) -> str:
    """Merge custom instructions with hook-provided instructions."""
    if not hook_instructions:
        return custom_instructions
    if not custom_instructions:
        return hook_instructions
    return f"{custom_instructions}\n\n{hook_instructions}"


def create_simple_summary_for_group(group: Any) -> dict[str, Any]:
    """Create a simple summary message for a MessageGroup.

    Used as a fallback when no compactor function is provided
    in selective compaction. Extracts the first few text blocks
    from the group's messages to form a brief summary.

    Args:
        group: A MessageGroup object (from grouping.py) with
            .messages, .label, .importance attributes.

    Returns:
        A system message dict with subtype "compact_boundary".
    """
    from hare.services.token_estimation import estimate_tokens

    messages = getattr(group, "messages", [])
    label = getattr(group, "label", "")
    importance = getattr(group, "importance", None)
    importance_str = importance.value if importance else "medium"

    summary_parts: list[str] = []
    for msg in messages[:10]:  # Sample first 10 messages
        msg_type = msg.get("type", "")
        content = msg.get("message", {}).get("content", "") if msg_type in ("user", "assistant") else msg.get("content", "")
        if isinstance(content, str) and content.strip():
            snippet = content[:300]
            summary_parts.append(f"[{msg_type}] {snippet}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        snippet = text[:300]
                        summary_parts.append(f"[{msg_type}] {snippet}")
                    break  # One text block per message is enough

    compacted_count = len(messages)
    label_prefix = f"[{label}] " if label else ""
    summary_text = "\n".join(summary_parts[:15])

    content = (
        f"{label_prefix}Compact summary ({importance_str} importance, "
        f"{compacted_count} messages):\n\n{summary_text}"
    )

    return {
        "type": "system",
        "subtype": "compact_boundary",
        "content": content,
        "compact_metadata": {
            "group_label": label,
            "importance": importance_str,
            "compacted_count": compacted_count,
        },
    }
