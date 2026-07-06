"""
Message creation, filtering and normalization utilities.

Port of: src/utils/messages.ts
"""

# mypy: disable-error-code="union-attr"
# This module works with discriminated unions (UserMessage | AssistantMessage | ...)
# where code already checks types before accessing fields like .message / .cost_usd.
# mypy's type narrowing does not propagate through the complex filter chains used here.

from __future__ import annotations

import re
from typing import Any, Optional, Sequence
from uuid import uuid4

from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    AttachmentMessage,
    Message,
    ProgressMessage,
    SystemMessage,
    ToolUseSummaryMessage,
    UserMessage,
)

SYNTHETIC_MESSAGES: set[str] = set()

NO_RESPONSE_REQUESTED = "No response requested."
INTERRUPT_MESSAGE = "[Request interrupted by user]"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "[Request interrupted by user for tool use]"
CANCEL_MESSAGE = (
    "The user doesn't want to take this action right now. "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)
REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected "
    "(eg. if it was a file edit, the new_string was NOT written to the file). "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)
SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"
SYNTHETIC_MODEL = "<synthetic>"

STRIPPED_TAGS_RE = re.compile(
    r"<(commit_analysis|context|function_analysis|pr_analysis)>.*?</\1>\n?",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Message creators
# ---------------------------------------------------------------------------


def create_user_message(
    *,
    content: Any,
    is_meta: bool = False,
    is_compact_summary: bool = False,
    tool_use_result: Optional[str] = None,
    source_tool_assistant_uuid: Optional[str] = None,
    uuid: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> UserMessage:
    return UserMessage(
        type="user",
        uuid=uuid or str(uuid4()),
        message=APIMessage(role="user", content=content),
        is_meta=is_meta,
        is_compact_summary=is_compact_summary,
        tool_use_result=tool_use_result,
        source_tool_assistant_uuid=source_tool_assistant_uuid,
        timestamp=timestamp or "",
    )


def create_user_interruption_message(*, tool_use: bool = False) -> UserMessage:
    content = INTERRUPT_MESSAGE_FOR_TOOL_USE if tool_use else INTERRUPT_MESSAGE
    return create_user_message(content=content, is_meta=True)


def create_system_message(content: str, subtype: str = "info") -> SystemMessage:
    return SystemMessage(
        type="system",
        uuid=str(uuid4()),
        subtype=subtype,
        content=content,
    )


def create_assistant_api_error_message(
    *,
    content: str,
    error: Optional[str] = None,
    error_details: Optional[str] = None,
) -> AssistantMessage:
    return AssistantMessage(
        type="assistant",
        uuid=str(uuid4()),
        message=APIMessage(
            role="assistant",
            content=[{"type": "text", "text": content}],
        ),
        is_api_error_message=True,
        api_error=error,
        error_details=error_details,
    )


def create_assistant_message(
    content: str | list[dict[str, Any]],
    uuid: Optional[str] = None,
) -> AssistantMessage:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    return AssistantMessage(
        type="assistant",
        uuid=uuid or str(uuid4()),
        message=APIMessage(role="assistant", content=content),
    )


def create_attachment_message(attachment: dict[str, Any]) -> AttachmentMessage:
    return AttachmentMessage(
        type="attachment",
        uuid=str(uuid4()),
        attachment=attachment,
    )


def create_tool_use_summary_message(
    summary: str, tool_use_ids: list[str]
) -> ToolUseSummaryMessage:
    return ToolUseSummaryMessage(
        type="tool_use_summary",
        uuid=str(uuid4()),
        summary=summary,
        preceding_tool_use_ids=tool_use_ids,
    )


def create_microcompact_boundary_message(
    trigger: str,
    tokens_freed: int,
    deleted_tokens: int,
    deleted_tool_ids: list[str],
    preserved_ids: list[str],
) -> SystemMessage:
    return SystemMessage(
        type="system",
        uuid=str(uuid4()),
        subtype="microcompact_boundary",
        content="Context microcompacted",
        compact_metadata={
            "trigger": trigger,
            "tokensFreed": tokens_freed,
            "deletedTokens": deleted_tokens,
            "deletedToolIds": deleted_tool_ids,
            "preservedIds": preserved_ids,
        },
    )


def create_stop_hook_summary_message(
    hook_count: int,
    hook_infos: list[Any],
    hook_errors: list[str],
    prevented_continuation: bool,
    stop_reason: str,
    has_output: bool,
    severity: str = "suggestion",
    tool_use_id: str = "",
) -> SystemMessage:
    summary = (
        f"Stop hooks: {hook_count} ran, {len(hook_errors)} error(s)."
        + (" Continuation prevented." if prevented_continuation else "")
        + (f" Reason: {stop_reason}" if stop_reason else "")
    )
    return SystemMessage(
        type="system",
        uuid=str(uuid4()),
        subtype=severity,
        content=summary,
        compact_metadata={
            "stopHookSummary": {
                "hookCount": hook_count,
                "hookInfos": hook_infos,
                "hookErrors": hook_errors,
                "preventedContinuation": prevented_continuation,
                "hasOutput": has_output,
                "toolUseID": tool_use_id,
            },
        },
    )


def create_progress_message(
    tool_use_id: str,
    data: Any,
    parent_tool_use_id: Optional[str] = None,
) -> ProgressMessage:
    return ProgressMessage(
        type="progress",
        tool_use_id=tool_use_id,
        data=data,
    )


def create_system_api_error_message(
    content: str,
    error: Optional[str] = None,
    subtype: str = "error",
) -> SystemMessage:
    return SystemMessage(
        type="system",
        uuid=str(uuid4()),
        subtype=subtype,
        content=content,
        error=error,
    )


def create_turn_duration_message(
    turn_count: int,
    duration_ms: int,
    message_count: int,
) -> SystemMessage:
    return SystemMessage(
        type="system",
        uuid=str(uuid4()),
        subtype="turn_duration",
        content=f"Turn {turn_count}: {duration_ms}ms, {message_count} messages",
        compact_metadata={
            "turnDuration": {
                "turnCount": turn_count,
                "durationMs": duration_ms,
                "messageCount": message_count,
            },
        },
    )


# ---------------------------------------------------------------------------
# Message type guards
# ---------------------------------------------------------------------------


def is_tool_use_request_message(message: Message) -> bool:
    if message.type != "assistant":
        return False
    content = message.message.content
    if not isinstance(content, list) or len(content) != 1:
        return False
    return (
        content[0].get("type") == "tool_use" if isinstance(content[0], dict) else False
    )


def is_tool_use_result_message(message: Message) -> bool:
    if message.type != "user":
        return False
    content = message.message.content
    if isinstance(content, str):
        return False
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def is_compact_boundary_message(message: Message) -> bool:
    return (
        message.type == "system"
        and getattr(message, "subtype", "") == "compact_boundary"
    )


def is_system_local_command_message(message: Message) -> bool:
    return (
        message.type == "system" and getattr(message, "subtype", "") == "local_command"
    )


def is_synthetic_api_error_message(message: Message) -> bool:
    if message.type != "assistant":
        return False
    return bool(getattr(message, "is_api_error_message", False))


# ---------------------------------------------------------------------------
# Filter functions (ported from messages.ts / conversationRecovery.ts)
# ---------------------------------------------------------------------------


# These filters run on both live Message objects (normalize path) and on plain
# transcript-envelope dicts (resume/--continue read path, from
# load_transcript_file). The accessors below read either shape so the resume
# pipeline — which is dict-based throughout — doesn't crash on dicts.
def _m_type(m: Any) -> Any:
    return m.get("type") if isinstance(m, dict) else getattr(m, "type", None)


def _m_api_message(m: Any) -> Any:
    return m.get("message") if isinstance(m, dict) else getattr(m, "message", None)


def _m_content(m: Any) -> Any:
    api = _m_api_message(m)
    if isinstance(api, dict):
        return api.get("content")
    return getattr(api, "content", None)


def _m_msg_id(m: Any) -> Any:
    api = _m_api_message(m)
    if isinstance(api, dict):
        return api.get("id")
    return getattr(api, "id", None)


def filter_unresolved_tool_uses(messages: list[Message]) -> list[Message]:
    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()

    for msg in messages:
        if _m_type(msg) not in ("user", "assistant"):
            continue
        content = _m_content(msg)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tid = block.get("id")
                if isinstance(tid, str):
                    tool_use_ids.add(tid)
            if block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                if isinstance(tid, str):
                    tool_result_ids.add(tid)

    unresolved_ids = tool_use_ids - tool_result_ids

    if not unresolved_ids:
        return messages

    def _keep(msg: Message) -> bool:
        if _m_type(msg) != "assistant":
            return True
        content = _m_content(msg)
        if not isinstance(content, list):
            return True
        tool_blocks = [
            b.get("id")
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if not tool_blocks:
            return True
        # Remove only if ALL tool_use blocks are unresolved
        return not all(tid in unresolved_ids for tid in tool_blocks)

    return [m for m in messages if _keep(m)]


def filter_orphaned_thinking_only_messages(
    messages: list[Message],
) -> list[Message]:
    """Filter orphaned thinking-only assistant messages.

    A thinking-only message is 'orphaned' if there is NO other assistant message
    with the same message.id that contains non-thinking content. If such a message
    exists, the thinking block will be merged with it in normalize_messages_for_api().

    Port of: filterOrphanedThinkingOnlyMessages in utils/messages.ts
    """
    # First pass: collect message.ids that have non-thinking content
    message_ids_with_non_thinking: set[str] = set()
    for m in messages:
        if _m_type(m) != "assistant":
            continue
        content = _m_content(m)
        if not isinstance(content, list):
            continue
        msg_id = _m_msg_id(m)
        has_non_thinking = any(
            isinstance(b, dict) and b.get("type") not in ("thinking", "redacted_thinking")
            for b in content
        )
        if has_non_thinking and msg_id:
            message_ids_with_non_thinking.add(msg_id)

    # Second pass: filter out truly orphaned thinking-only messages
    out: list[Message] = []
    for m in messages:
        if _m_type(m) != "assistant":
            out.append(m)
            continue
        content = _m_content(m)
        if not isinstance(content, list):
            out.append(m)
            continue
        if not content:
            out.append(m)
            continue
        all_thinking = all(
            isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking")
            for b in content
        )
        if not all_thinking:
            out.append(m)
            continue
        # It's thinking-only. Keep it if there's another message with same id
        # that has non-thinking content (they'll be merged later).
        msg_id = _m_msg_id(m)
        if msg_id and msg_id in message_ids_with_non_thinking:
            out.append(m)
            continue
        # Truly orphaned — drop it
    return out


def filter_whitespace_only_assistant_messages(
    messages: list[Message],
) -> list[Message]:
    out: list[Message] = []
    for m in messages:
        if _m_type(m) != "assistant":
            out.append(m)
            continue
        content = _m_content(m)
        if not isinstance(content, list):
            out.append(m)
            continue
        text_blocks = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        if text_blocks and all(
            isinstance(t, str) and not t.strip() for t in text_blocks
        ):
            continue
        out.append(m)
    return out


def filter_trailing_thinking_from_last_assistant(
    messages: list[Message],
) -> list[Message]:
    if not messages:
        return messages
    last_idx = len(messages) - 1
    if messages[last_idx].type != "assistant":
        return messages

    m = messages[last_idx]
    content = m.message.content
    if not isinstance(content, list):
        return messages

    new_content: list[dict[str, Any]] = []
    found_non_thinking = False
    for b in reversed(content):
        if isinstance(b, dict) and b.get("type") in (
            "thinking",
            "redacted_thinking",
        ):
            if not found_non_thinking:
                continue
        found_non_thinking = True
        new_content.append(b)
    new_content.reverse()

    if len(new_content) != len(content):
        new_msg = AssistantMessage(
            type=m.type,  # type: ignore[arg-type]  # narrowed to AssistantMessage upstream
            uuid=m.uuid,
            timestamp=m.timestamp,
            message=APIMessage(
                role=m.message.role,
                content=new_content,
                id=getattr(m.message, "id", None),
                stop_reason=getattr(m.message, "stop_reason", None),
                usage=getattr(m.message, "usage", None),
            ),
            cost_usd=m.cost_usd,
            duration_ms=m.duration_ms,
            is_api_error_message=m.is_api_error_message,
            api_error=m.api_error,
        )
        result = list(messages)
        result[last_idx] = new_msg
        return result
    return messages


# ---------------------------------------------------------------------------
# Text extraction utilities
# ---------------------------------------------------------------------------


def extract_text_content(blocks: Any, separator: str = "") -> str:
    if isinstance(blocks, str):
        return blocks
    if isinstance(blocks, list):
        return separator.join(
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(blocks)


def get_content_text(content: str | list[dict[str, Any]] | None) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        t = extract_text_content(content, "\n").strip()
        return t or None
    return None


def get_assistant_message_text(message: Message) -> str | None:
    if message.type != "assistant":
        return None
    content = message.message.content
    if isinstance(content, list):
        return extract_text_content(content, "\n").strip() or None
    return None


def get_user_message_text(message: Message) -> str | None:
    if message.type != "user":
        return None
    return get_content_text(message.message.content)


def extract_tag(html: str, tag_name: str) -> str | None:
    if not html.strip() or not tag_name.strip():
        return None
    import re as _re

    esc = _re.escape(tag_name)
    pat = _re.compile(rf"<{esc}(?:\s[^>]*)?>([\s\S]*?)</{esc}>", _re.I)
    m = pat.search(html)
    return m.group(1) if m else None


def strip_prompt_xml_tags(content: str) -> str:
    return STRIPPED_TAGS_RE.sub("", content).strip()


def is_empty_message_text(text: str) -> bool:
    return not strip_prompt_xml_tags(text).strip()


# ---------------------------------------------------------------------------
# UUID derivation
# ---------------------------------------------------------------------------

BASE36_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def derive_short_message_id(uuid_str: str) -> str:
    h = uuid_str.replace("-", "")[:10]
    n = int(h, 16)
    if n == 0:
        return "0"
    chars = []
    while n:
        n, r = divmod(n, 36)
        chars.append(BASE36_CHARS[r])
    return "".join(reversed(chars))[:6]


def derive_uuid(parent_uuid: str, index: int) -> str:
    """Deterministic UUID derivation from parent UUID + index."""
    import hashlib
    import uuid as _uuid_mod

    combined = f"{parent_uuid}:{index}"
    hash_bytes = hashlib.sha256(combined.encode()).digest()[:16]
    return str(_uuid_mod.UUID(bytes=hash_bytes))


# ---------------------------------------------------------------------------
# Message normalization
# ---------------------------------------------------------------------------


def normalize_messages(messages: list[Message]) -> list[Message]:
    """Split multi-block messages into single-block messages."""
    is_new_chain = False
    result: list[Message] = []

    for message in messages:
        if message.type == "assistant":
            content = message.message.content
            if not isinstance(content, list):
                result.append(message)
                continue
            is_new_chain = is_new_chain or len(content) > 1
            for index, block in enumerate(content):
                new_uuid = (
                    derive_uuid(message.uuid, index) if is_new_chain else message.uuid
                )
                result.append(
                    AssistantMessage(
                        type="assistant",
                        uuid=new_uuid,
                        timestamp=getattr(message, "timestamp", ""),
                        message=APIMessage(
                            role="assistant",
                            content=[block],
                        ),
                        is_api_error_message=getattr(
                            message, "is_api_error_message", False
                        ),
                        api_error=getattr(message, "api_error", None),
                    )
                )
        elif message.type == "user":
            content = message.message.content
            if isinstance(content, str):
                new_uuid = (
                    derive_uuid(message.uuid, 0) if is_new_chain else message.uuid
                )
                result.append(
                    create_user_message(
                        content=[{"type": "text", "text": content}],
                        is_meta=getattr(message, "is_meta", False),
                        uuid=new_uuid,
                        timestamp=getattr(message, "timestamp", None),
                    )
                )
            elif isinstance(content, list):
                is_new_chain = is_new_chain or len(content) > 1
                for index, block in enumerate(content):
                    new_uuid = (
                        derive_uuid(message.uuid, index)
                        if is_new_chain
                        else message.uuid
                    )
                    result.append(
                        create_user_message(
                            content=[block],
                            is_meta=getattr(message, "is_meta", False),
                            uuid=new_uuid,
                            timestamp=getattr(message, "timestamp", None),
                        )
                    )
            else:
                result.append(message)
        else:
            result.append(message)

    return result


def normalize_messages_for_api(
    messages: list[Message],
    tools: Sequence[Any] = (),
) -> list[Message]:
    """Prepare messages for API submission.

    Filters progress/system messages, merges consecutive user/assistant
    messages, converts attachments and local_command system messages to
    user messages, and applies trailing-thinking and whitespace filtering.
    """
    # Filter: drop progress and non-local_command system messages
    filtered: list[Message] = []
    for m in messages:
        if m.type == "progress":
            continue
        if m.type == "system" and not is_system_local_command_message(m):
            continue
        if is_synthetic_api_error_message(m):
            continue
        filtered.append(m)

    result: list[Message] = []
    for message in filtered:
        if message.type == "system" and is_system_local_command_message(message):
            # Convert local_command system msg to user msg
            user_msg = create_user_message(content=getattr(message, "content", ""))
            if result and result[-1].type == "user":
                result[-1] = _merge_user_messages(result[-1], user_msg)
            else:
                result.append(user_msg)
        elif message.type == "user":
            if result and result[-1].type == "user":
                result[-1] = _merge_user_messages(result[-1], message)
            else:
                result.append(message)
        elif message.type == "assistant":
            # Merge with previous assistant if same message.id.
            # Walk backwards skipping tool_result user messages and different-ID
            # assistants, since concurrent agents can interleave streaming content
            # blocks from multiple API responses with different message IDs.
            # (Port of TS normalizeMessagesForAPI assistant merge logic)
            merged = False
            msg_id = getattr(message.message, "id", None)
            for i in range(len(result) - 1, -1, -1):
                prev = result[i]
                # Stop if we hit something that's neither an assistant nor a tool_result user msg
                is_tool_result_msg = (
                    prev.type == "user"
                    and isinstance(getattr(prev.message, "content", None), list)
                    and any(
                        isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in prev.message.content
                    )
                )
                if prev.type != "assistant" and not is_tool_result_msg:
                    break
                if prev.type == "assistant":
                    prev_id = getattr(prev.message, "id", None)
                    if msg_id and prev_id == msg_id:
                        result[i] = _merge_assistant_messages(prev, message)
                        merged = True
                        break
            if not merged:
                result.append(message)
        elif message.type == "attachment":
            # Attachments become user messages
            att_msg = _attachment_to_user_message(message)
            if result and result[-1].type == "user":
                result[-1] = _merge_user_messages(result[-1], att_msg)
            else:
                result.append(att_msg)
        else:
            result.append(message)

    # Post-processing: strip trailing thinking, filter orphans and whitespace
    result = list(filter_orphaned_thinking_only_messages(result))
    result = filter_trailing_thinking_from_last_assistant(result)
    result = list(filter_whitespace_only_assistant_messages(result))
    result = _ensure_non_empty_assistant_content(result)

    return result


def _merge_user_messages(a: Message, b: Message) -> Message:
    a_content = a.message.content
    b_content = b.message.content

    if isinstance(a_content, str):
        a_content = [{"type": "text", "text": a_content}]
    if isinstance(b_content, str):
        b_content = [{"type": "text", "text": b_content}]

    a_list = list(a_content) if isinstance(a_content, list) else [a_content]
    b_list = list(b_content) if isinstance(b_content, list) else [b_content]

    # Join text at seam if both ends are text
    if (
        a_list
        and b_list
        and isinstance(a_list[-1], dict)
        and a_list[-1].get("type") == "text"
        and isinstance(b_list[0], dict)
        and b_list[0].get("type") == "text"
    ):
        a_list[-1] = {
            **a_list[-1],
            "text": a_list[-1]["text"] + "\n" + b_list[0]["text"],
        }
        merged_content = a_list + b_list[1:]
    else:
        merged_content = a_list + b_list

    # Hoist tool_results to front
    trs = [
        b
        for b in merged_content
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    others = [
        b
        for b in merged_content
        if not (isinstance(b, dict) and b.get("type") == "tool_result")
    ]
    final_content = trs + others

    new_uuid = b.uuid if getattr(a, "is_meta", False) else a.uuid
    return create_user_message(
        content=final_content,
        is_meta=getattr(a, "is_meta", False) and getattr(b, "is_meta", False),
        uuid=new_uuid,
    )


def _merge_assistant_messages(a: Message, b: Message) -> Message:
    a_content = (
        list(a.message.content)
        if isinstance(a.message.content, list)
        else [a.message.content]
    )
    b_content = (
        list(b.message.content)
        if isinstance(b.message.content, list)
        else [b.message.content]
    )
    return AssistantMessage(
        type="assistant",
        uuid=a.uuid,
        timestamp=getattr(a, "timestamp", ""),
        message=APIMessage(
            role="assistant",
            content=a_content + b_content,
            id=getattr(a.message, "id", None) or getattr(b.message, "id", None),
            stop_reason=getattr(b.message, "stop_reason", None)
            or getattr(a.message, "stop_reason", None),
            usage=getattr(b.message, "usage", None) or getattr(a.message, "usage", None),
        ),
        is_api_error_message=getattr(a, "is_api_error_message", False),
        api_error=getattr(a, "api_error", None),
    )


def _attachment_to_user_message(message: Message) -> Message:
    attachment = getattr(message, "attachment", {})
    if isinstance(attachment, dict):
        return create_user_message(
            content=f"[Attachment: {attachment.get('type', 'unknown')}]",
            is_meta=True,
        )
    return create_user_message(content="[Attachment]", is_meta=True)


def _ensure_non_empty_assistant_content(
    messages: list[Message],
) -> list[Message]:
    result: list[Message] = []
    for m in messages:
        if m.type == "assistant":
            content = m.message.content
            if not isinstance(content, list) or len(content) == 0:
                new_msg = AssistantMessage(
                    type="assistant",
                    uuid=m.uuid,
                    timestamp=getattr(m, "timestamp", ""),
                    message=APIMessage(
                        role="assistant",
                        content=[{"type": "text", "text": "."}],
                    ),
                    is_api_error_message=getattr(m, "is_api_error_message", False),
                    api_error=getattr(m, "api_error", None),
                )
                result.append(new_msg)
                continue
        result.append(m)
    return result


# ---------------------------------------------------------------------------
# Compact boundary helpers
# ---------------------------------------------------------------------------


def get_messages_after_compact_boundary(messages: list[Message]) -> list[Message]:
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if (
            getattr(msg, "type", "") == "system"
            and getattr(msg, "subtype", "") == "compact_boundary"
        ):
            return messages[i:]
    return messages


def find_last_compact_boundary_index(messages: list[Message]) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary_message(messages[i]):
            return i
    return -1


# ---------------------------------------------------------------------------
# Content stripping
# ---------------------------------------------------------------------------


def strip_signature_blocks(messages: list[Message]) -> list[Message]:
    """Strip signature/thinking/redacted_thinking/connector_text blocks from
    all assistant messages."""
    result: list[Message] = []
    for m in messages:
        if m.type != "assistant":
            result.append(m)
            continue
        content = m.message.content
        if not isinstance(content, list):
            result.append(m)
            continue
        new_content = [
            b
            for b in content
            if isinstance(b, dict)
            and b.get("type") not in ("thinking", "redacted_thinking", "connector_text")
        ]
        if len(new_content) != len(content):
            new_msg = AssistantMessage(
                type="assistant",
                uuid=m.uuid,
                timestamp=getattr(m, "timestamp", ""),
                message=APIMessage(role="assistant", content=new_content),
                is_api_error_message=getattr(m, "is_api_error_message", False),
                api_error=getattr(m, "api_error", None),
            )
            result.append(new_msg)
        else:
            result.append(m)
    return result


# ---------------------------------------------------------------------------
# Tool call counting
# ---------------------------------------------------------------------------


def count_tool_calls(messages: list[Message], tool_name: str) -> int:
    count = 0
    for msg in messages:
        if msg.type == "assistant" and isinstance(msg.message.content, list):
            for block in msg.message.content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("name") == tool_name:
                        count += 1
    return count


# ---------------------------------------------------------------------------
# Ensure tool result pairing
# ---------------------------------------------------------------------------


def ensure_tool_result_pairing(
    messages: list[Message],
) -> list[Message]:
    """Repair tool_use/tool_result pairing mismatches before API submission.

    Handles three cases mirroring TS ensureToolResultPairing (CC-1212):
    1. Duplicate tool_use ids across messages — strip the duplicate block.
    2. tool_use with no matching tool_result — insert synthetic error result.
    3. tool_result with no matching tool_use (orphan) — strip the block.
    """
    result: list[Message] = []
    repaired = False
    # Cross-message dedup: same tool_use id in two different assistant messages.
    all_seen_tool_use_ids: set[str] = set()

    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.type != "assistant":
            # Strip orphaned tool_result blocks from user messages that have no
            # preceding assistant message in the output.
            if (
                msg.type == "user"
                and isinstance(msg.message.content, list)
                and (not result or result[-1].type != "assistant")
            ):
                stripped = [
                    b
                    for b in msg.message.content
                    if not (isinstance(b, dict) and b.get("type") == "tool_result")
                ]
                if len(stripped) != len(msg.message.content):
                    repaired = True
                    content = stripped if stripped else (
                        [{"type": "text", "text": "[Orphaned tool result removed]"}]
                        if not result
                        else None
                    )
                    if content is not None:
                        result.append(
                            _replace_message_content(msg, content)
                        )
                    i += 1
                    continue
            result.append(msg)
            i += 1
            continue

        # --- assistant message ---
        content = msg.message.content
        if not isinstance(content, list):
            result.append(msg)
            i += 1
            continue

        # Dedupe tool_use blocks by id (cross-message).
        seen_this_msg: set[str] = set()
        final_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tid = block.get("id", "")
                if tid and tid in all_seen_tool_use_ids:
                    repaired = True
                    continue
                if tid:
                    all_seen_tool_use_ids.add(tid)
                    seen_this_msg.add(tid)
            final_content.append(block)

        if not final_content:
            final_content = [{"type": "text", "text": "[Tool use interrupted]"}]

        assistant_msg = (
            _replace_message_content(msg, final_content)
            if len(final_content) != len(content)
            else msg
        )
        result.append(assistant_msg)

        # Collect surviving tool_use ids for this assistant turn.
        tool_use_ids = list(seen_this_msg)

        # Inspect the next user message for tool_results.
        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        existing_tr_ids: set[str] = set()
        duplicate_tr = False

        if next_msg is not None and next_msg.type == "user" and isinstance(
            next_msg.message.content, list
        ):
            seen_tr: set[str] = set()
            for block in next_msg.message.content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tr_id = block.get("tool_use_id", "")
                    if tr_id in seen_tr:
                        duplicate_tr = True
                    seen_tr.add(tr_id)
                    existing_tr_ids.add(tr_id)

        tool_use_id_set = set(tool_use_ids)
        missing_ids = [tid for tid in tool_use_ids if tid not in existing_tr_ids]
        orphaned_ids = {tid for tid in existing_tr_ids if tid not in tool_use_id_set}

        if not missing_ids and not orphaned_ids and not duplicate_tr:
            i += 1
            continue

        repaired = True
        synthetic_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tid,
                "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                "is_error": True,
            }
            for tid in missing_ids
        ]

        if next_msg is not None and next_msg.type == "user":
            next_content: list = (
                list(next_msg.message.content)
                if isinstance(next_msg.message.content, list)
                else [{"type": "text", "text": next_msg.message.content}]
            )
            if orphaned_ids or duplicate_tr:
                seen_tr2: set[str] = set()
                filtered = []
                for block in next_content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tr_id = block.get("tool_use_id", "")
                        if tr_id in orphaned_ids or tr_id in seen_tr2:
                            continue
                        seen_tr2.add(tr_id)
                    filtered.append(block)
                next_content = filtered

            patched_content = synthetic_blocks + next_content
            if patched_content:
                result.append(_replace_message_content(next_msg, patched_content))
            else:
                result.append(
                    create_user_message(
                        content="[No content]",
                        is_meta=True,
                    )
                )
            i += 2
        else:
            if synthetic_blocks:
                result.append(
                    create_user_message(
                        content=synthetic_blocks,
                        is_meta=True,
                    )
                )
            i += 1

    return result


def _replace_message_content(msg: Message, content: list) -> Message:
    """Return a copy of msg with message.content replaced."""
    from dataclasses import replace as dc_replace
    new_inner = dc_replace(msg.message, content=content)
    return dc_replace(msg, message=new_inner)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def get_last_assistant_message(
    messages: list[Message],
) -> Optional[Message]:
    for m in reversed(messages):
        if m.type == "assistant":
            return m
    return None


def has_tool_calls_in_last_assistant_turn(
    messages: list[Message],
) -> bool:
    for m in reversed(messages):
        if m.type != "assistant":
            continue
        content = m.message.content
        if not isinstance(content, list):
            return False
        return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    return False


# ---------------------------------------------------------------------------
# Image validation for API
# ---------------------------------------------------------------------------

# Max image file size: 5 MB (Anthropic API limit)
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Max image dimensions for many-image requests
_MAX_IMAGE_DIMENSION_MANY = 2000


def validate_images_for_api(messages: list[Message]) -> list[str]:
    """Validate all image content blocks in messages against API limits.

    Returns a list of error strings for any invalid images.
    Returns an empty list if all images are valid.

    Checks:
    - Image file size (from base64 data)
    - Image dimensions (from metadata in image blocks)
    """
    errors: list[str] = []
    for msg in messages:
        if msg.type not in ("user", "assistant"):
            continue
        content = getattr(msg.message, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in ("image", "image_url"):
                continue
            source = block.get("source", {})
            if isinstance(source, dict):
                data = source.get("data", "")
                if isinstance(data, str):
                    # Base64 data: ~4/3 * len
                    estimated_bytes = (len(data) * 3) // 4
                    if estimated_bytes > _MAX_IMAGE_BYTES:
                        errors.append(
                            f"Image exceeds {_MAX_IMAGE_BYTES // (1024*1024)} MB limit "
                            f"({estimated_bytes // (1024*1024)} MB)"
                        )
                # Check dimensions
                dims = source.get("dimensions")
                if isinstance(dims, dict):
                    width = dims.get("width", 0)
                    height = dims.get("height", 0)
                    if width > _MAX_IMAGE_DIMENSION_MANY or height > _MAX_IMAGE_DIMENSION_MANY:
                        errors.append(
                            f"Image dimensions ({width}x{height}) exceed "
                            f"{_MAX_IMAGE_DIMENSION_MANY}px limit for many-image requests"
                        )
    return errors


def strip_document_images_from_error_messages(
    messages: list[Message],
) -> list[Message]:
    """Strip large images/documents from messages after image/PDF errors.

    When the API returns image/PDF size errors, walk backward from the error
    to find the preceding user message and strip the offending media blocks.
    """
    import re

    result: list[Message] = list(messages)
    pdf_page_re = re.compile(r"maximum of \d+ PDF pages")

    for i, msg in enumerate(result):
        if msg.type != "assistant" or not getattr(msg, "is_api_error_message", False):
            continue
        content = getattr(msg.message, "content", None)
        if not isinstance(content, list):
            continue
        error_text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                error_text = str(block.get("text", ""))
                break

        # Check if this is a media-size error
        is_media = (
            ("image exceeds" in error_text and "maximum" in error_text)
            or ("image dimensions exceed" in error_text)
            or bool(pdf_page_re.search(error_text))
        )
        if not is_media:
            continue

        # Walk backward to find the preceding user message with media
        for j in range(i - 1, -1, -1):
            prev = result[j]
            if prev.type != "user":
                continue
            prev_content = getattr(prev.message, "content", None)
            if not isinstance(prev_content, list):
                continue
            # Strip image and document blocks
            stripped = [
                b
                for b in prev_content
                if isinstance(b, dict)
                and b.get("type") not in ("image", "image_url", "document")
            ]
            if len(stripped) < len(prev_content):
                # Create replacement message with stripped content
                import copy
                new_msg = copy.deepcopy(prev)
                new_msg.message.content = stripped
                result[j] = new_msg
                break

    return result


def auto_reject_message(tool_name: str) -> str:
    return f"Permission to use {tool_name} has been denied."
