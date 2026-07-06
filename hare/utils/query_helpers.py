"""
Query / SDK message normalization helpers. Port of src/utils/queryHelpers.ts (subset).
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any
from uuid import uuid4 as _uuid4

from hare.bootstrap.state import is_session_persistence_disabled
from hare.tools_impl.BashTool.prompt import BASH_TOOL_NAME
from hare.tools_impl.FileEditTool.prompt import FILE_EDIT_TOOL_NAME
from hare.tools_impl.FileReadTool.prompt import FILE_READ_TOOL_NAME, FILE_UNCHANGED_STUB
from hare.tools_impl.FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
from hare.utils.file import get_file_modification_time, read_file_sync_with_metadata
from hare.utils.file_state_cache import (
    FileState,
    FileStateCache,
    create_file_state_cache_with_size_limit,
)
from hare.utils.path_utils import expand_path

ASK_READ_FILE_STATE_CACHE_SIZE = 10

_SYSTEM_REMINDER = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")


def _is_fs_inaccessible(exc: BaseException) -> bool:
    return isinstance(exc, (FileNotFoundError, PermissionError, OSError))


def strip_line_number_prefix(line: str) -> str:
    m = re.match(r"^\s*\d+[\u2192\t](.*)$", line)
    return m.group(1) if m else line


def is_result_successful(message: Any | None, stop_reason: str | None = None) -> bool:
    if message is None:
        return False
    t = getattr(message, "type", None)
    if t == "assistant":
        content = getattr(getattr(message, "message", None), "content", None)
        if isinstance(content, list) and content:
            last = content[-1]
            if isinstance(last, dict):
                return last.get("type") in ("text", "thinking", "redacted_thinking")
        return False
    if t == "user":
        content = getattr(getattr(message, "message", None), "content", None)
        if isinstance(content, list) and content:
            return all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
    return stop_reason == "end_turn"


async def normalize_message(message: Any) -> AsyncGenerator[dict[str, Any], None]:
    """Map an internal ``Message`` object to one or more SDK-compatible dict shapes.

    Handles all message types (assistant, user, system, progress, attachment,
    stream_event, tombstone, tool_use_summary).  Multi-block assistant and user
    messages are split into individual events so that downstream consumers
    receive a flat sequence of single-block messages.

    Yields a dict with at minimum ``type``, ``uuid``, ``session_id``, and
    ``message`` keys.  Additional fields (subtype, attachment, timestamp, etc.)
    are included when present on the source message.
    """
    try:
        from hare.utils.messages import get_session_id
    except ImportError:
        def _get_session_id() -> str:
            from uuid import uuid4
            return str(uuid4())
    else:
        _get_session_id = get_session_id

    session_id = _get_session_id()
    msg_type: str = (
        message.get("type") if isinstance(message, dict)
        else getattr(message, "type", "unknown")
    )
    msg_uuid: str = (
        message.get("uuid", "")
        if isinstance(message, dict)
        else getattr(message, "uuid", "")
    )
    timestamp: str = (
        message.get("timestamp", "")
        if isinstance(message, dict)
        else getattr(message, "timestamp", "")
    )

    # ── assistant ──────────────────────────────────────────────────────────
    if msg_type == "assistant":
        content = _get_message_content(message)
        if content is not None and len(content) > 1:
            # Split multi-block assistant messages into individual events
            for block in content:
                yield {
                    "type": "assistant",
                    "uuid": msg_uuid,
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "message": {
                        "role": "assistant",
                        "content": [block],
                    },
                }
            return
        yield {
            "type": "assistant",
            "uuid": msg_uuid,
            "session_id": session_id,
            "timestamp": timestamp,
            "message": {
                "role": "assistant",
                "content": content or [],
            },
        }
        return

    # ── user ────────────────────────────────────────────────────────────────
    if msg_type == "user":
        content = _get_message_content(message)
        if content is not None and len(content) > 1:
            for block in content:
                yield {
                    "type": "user",
                    "uuid": msg_uuid,
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "message": {
                        "role": "user",
                        "content": [block],
                    },
                }
            return
        raw = (
            message.get("message", {}).get("content", "")
            if isinstance(message, dict)
            else getattr(getattr(message, "message", None), "content", "")
        )
        if isinstance(raw, str):
            raw = _SYSTEM_REMINDER.sub("", raw).strip()
        yield {
            "type": "user",
            "uuid": msg_uuid,
            "session_id": session_id,
            "timestamp": timestamp,
            "message": {
                "role": "user",
                "content": content if content is not None else raw,
            },
            "is_meta": (
                message.get("is_meta", False)
                if isinstance(message, dict)
                else getattr(message, "is_meta", False)
            ),
        }
        return

    # ── system ──────────────────────────────────────────────────────────────
    if msg_type == "system":
        subtype = (
            message.get("subtype", "")
            if isinstance(message, dict)
            else getattr(message, "subtype", "")
        )
        content = (
            message.get("content", "")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        )
        yield {
            "type": "system",
            "subtype": subtype,
            "uuid": msg_uuid,
            "session_id": session_id,
            "timestamp": timestamp,
            "content": content,
        }
        return

    # ── progress ────────────────────────────────────────────────────────────
    if msg_type == "progress":
        yield {
            "type": "progress",
            "uuid": msg_uuid,
            "session_id": session_id,
            "timestamp": timestamp,
            "tool_use_id": (
                message.get("tool_use_id", "")
                if isinstance(message, dict)
                else getattr(message, "tool_use_id", "")
            ),
            "data": (
                message.get("data", None)
                if isinstance(message, dict)
                else getattr(message, "data", None)
            ),
        }
        return

    # ── attachment ──────────────────────────────────────────────────────────
    if msg_type == "attachment":
        attachment = (
            message.get("attachment", {})
            if isinstance(message, dict)
            else getattr(message, "attachment", {})
        )
        yield {
            "type": "attachment",
            "uuid": msg_uuid,
            "session_id": session_id,
            "timestamp": timestamp,
            "attachment": attachment if isinstance(attachment, dict) else {},
        }
        return

    # ── stream_event ────────────────────────────────────────────────────────
    if msg_type == "stream_event":
        event = (
            message.get("event", {})
            if isinstance(message, dict)
            else getattr(message, "event", {})
        )
        yield {
            "type": "stream_event",
            "session_id": session_id,
            "event": event if isinstance(event, dict) else {},
        }
        return

    # ── tombstone ───────────────────────────────────────────────────────────
    if msg_type == "tombstone":
        nested: Any = (
            message.get("message")
            if isinstance(message, dict)
            else getattr(message, "message", None)
        )
        yield {
            "type": "tombstone",
            "session_id": session_id,
            "message": nested,
        }
        return

    # ── tool_use_summary ────────────────────────────────────────────────────
    if msg_type == "tool_use_summary":
        yield {
            "type": "tool_use_summary",
            "uuid": msg_uuid,
            "session_id": session_id,
            "summary": (
                message.get("summary", "")
                if isinstance(message, dict)
                else getattr(message, "summary", "")
            ),
            "preceding_tool_use_ids": (
                message.get("preceding_tool_use_ids", [])
                if isinstance(message, dict)
                else getattr(message, "preceding_tool_use_ids", [])
            ),
        }
        return

    # ── fallback ────────────────────────────────────────────────────────────
    yield {
        "type": msg_type,
        "uuid": msg_uuid,
        "session_id": session_id,
        "timestamp": timestamp,
        "message": message,
    }


async def handle_orphaned_permission(
    orphaned_permission: Any,
    tools: Any,
    mutable_messages: list[Any],
    process_user_input_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    """Resume tool execution after a permission prompt has been answered.

    When a tool invocation requires permission and the user responds, this
    function replays the orphaned tool-use blocks through the tool
    orchestration layer so that the results are appended to the conversation
    as normal ``tool_result`` user messages.

    Parameters
    ----------
    orphaned_permission:
        The permission object emitted by the agent loop.  Must carry at least
        ``tool_use_ids`` (list of str) and ``assistant_message`` (the
        ``AssistantMessage`` that triggered the prompt).
    tools:
        The Tool registry (list of ``Tool`` instances).
    mutable_messages:
        The mutable message list for the conversation (modified in-place).
    process_user_input_context:
        The ``ToolUseContext`` snapshot at the time of the permission prompt.
    """
    try:
        from hare.services.tools.tool_orchestration import run_tools
    except ImportError:
        return

    # ------------------------------------------------------------------
    # 1.  Validate the orphaned permission shape
    # ------------------------------------------------------------------
    tool_use_ids: list[str] = []
    assistant_message: Any = None

    if isinstance(orphaned_permission, dict):
        raw_ids = orphaned_permission.get("tool_use_ids", [])
        if isinstance(raw_ids, list):
            tool_use_ids = [str(tid) for tid in raw_ids]
        assistant_message = orphaned_permission.get("assistant_message")
    else:
        raw_ids = getattr(orphaned_permission, "tool_use_ids", [])
        if raw_ids is not None:
            tool_use_ids = [str(tid) for tid in raw_ids]
        assistant_message = getattr(orphaned_permission, "assistant_message", None)

    if not tool_use_ids or assistant_message is None:
        # Nothing to resume – yield an empty user message to unblock the loop
        yield {
            "type": "user",
            "message": {"role": "user", "content": "[Orphaned permission – no tool uses to resume]"},
        }
        return

    # ------------------------------------------------------------------
    # 2.  Collect the relevant tool_use blocks from the assistant message
    # ------------------------------------------------------------------
    assistant_blocks = get_tool_use_blocks(assistant_message)
    orphaned_blocks: list[dict[str, Any]] = [
        b for b in assistant_blocks
        if b.get("id") in tool_use_ids
    ]
    if not orphaned_blocks:
        yield {
            "type": "user",
            "message": {"role": "user", "content": "[Orphaned permission – tool use blocks not found]"},
        }
        return

    # ------------------------------------------------------------------
    # 3.  Build a minimal ToolUseContext from the snapshot
    # ------------------------------------------------------------------
    from hare.tool import ToolUseContext, CanUseToolFn

    if isinstance(process_user_input_context, dict):
        # Reconstruct from dict (best-effort)
        tool_ctx: Any = process_user_input_context
    else:
        tool_ctx = process_user_input_context

    # Build a passthrough can_use_tool that approves everything – the user
    # already granted permission for these specific tool calls.
    async def _always_allow(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"behavior": "allow", "updatedInput": {}}

    can_use_tool: CanUseToolFn = _always_allow

    # ------------------------------------------------------------------
    # 4.  Run the orphaned blocks through tool orchestration
    # ------------------------------------------------------------------
    assistant_messages_for_run: list[Any] = [assistant_message]
    try:
        async for update in run_tools(
            tool_use_messages=orphaned_blocks,
            assistant_messages=assistant_messages_for_run,
            can_use_tool=can_use_tool,
            tool_use_context=tool_ctx,
        ):
            # Update in-flight context if new_context is provided
            if hasattr(update, "new_context") and update.new_context is not None:
                tool_ctx = update.new_context

            msg = getattr(update, "message", None)
            if msg is not None:
                mutable_messages.append(msg)
                async for norm_msg in normalize_message(msg):
                    yield norm_msg
    except Exception as exc:
        # If tool execution fails, yield an error tool_result for each
        # orphaned block so the conversation can continue.
        for block in orphaned_blocks:
            tid = block.get("id", "unknown")
            err_msg = create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": f"Error resuming orphaned tool after permission: {exc}",
                        "is_error": True,
                    }
                ],
            )
            setattr(err_msg, "uuid", str(_uuid4()))  # type: ignore[attr-defined]
            mutable_messages.append(err_msg)
            async for norm_msg in normalize_message(err_msg):
                yield norm_msg


def extract_read_files_from_messages(
    messages: list[Any],
    cwd: str,
    max_size: int = ASK_READ_FILE_STATE_CACHE_SIZE,
) -> FileStateCache:
    cache = create_file_state_cache_with_size_limit(max_size)
    file_read_ids: dict[str, str] = {}
    file_write_ids: dict[str, dict[str, str]] = {}
    file_edit_ids: dict[str, str] = {}
    for message in messages:
        if getattr(message, "type", None) != "assistant":
            continue
        content = getattr(getattr(message, "message", None), "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            tid = block.get("id")
            inp = block.get("input") or {}
            if name == FILE_READ_TOOL_NAME and tid:
                if (
                    inp.get("file_path")
                    and inp.get("offset") is None
                    and inp.get("limit") is None
                ):
                    file_read_ids[tid] = expand_path(inp["file_path"], cwd)
            elif name == FILE_WRITE_TOOL_NAME and tid:
                if inp.get("file_path") and inp.get("content") is not None:
                    file_write_ids[tid] = {
                        "filePath": expand_path(inp["file_path"], cwd),
                        "content": str(inp.get("content", "")),
                    }
            elif name == FILE_EDIT_TOOL_NAME and tid:
                fp = inp.get("file_path")
                if fp:
                    file_edit_ids[tid] = expand_path(fp, cwd)
    _ = is_session_persistence_disabled

    def _ts_ms(ts: Any) -> float:
        if isinstance(ts, (int, float)):
            return float(ts)
        try:
            return (
                datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except Exception:
            return 0.0

    for message in messages:
        if getattr(message, "type", None) != "user":
            continue
        content = getattr(getattr(message, "message", None), "content", None)
        if not isinstance(content, list):
            continue
        ts = _ts_ms(getattr(message, "timestamp", None))
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tuid = block.get("tool_use_id")
            if not tuid:
                continue
            body = block.get("content")
            if isinstance(body, str) and tuid in file_read_ids:
                fp = file_read_ids[tuid]
                if body.startswith(FILE_UNCHANGED_STUB):
                    continue
                cleaned = _SYSTEM_REMINDER.sub("", body)
                file_content = "\n".join(
                    strip_line_number_prefix(ln) for ln in cleaned.split("\n")
                ).strip()
                cache.set(
                    fp,
                    FileState(
                        content=file_content, timestamp=ts, offset=None, limit=None
                    ),
                )
            wt = file_write_ids.get(tuid)
            if wt:
                cache.set(
                    wt["filePath"],
                    FileState(
                        content=wt["content"], timestamp=ts, offset=None, limit=None
                    ),
                )
            ed = file_edit_ids.get(tuid)
            if ed and block.get("is_error") is not True:
                try:
                    disk = read_file_sync_with_metadata(ed)
                    cache.set(
                        ed,
                        FileState(
                            content=disk["content"],
                            timestamp=float(get_file_modification_time(ed)),
                            offset=None,
                            limit=None,
                        ),
                    )
                except OSError as e:
                    if not _is_fs_inaccessible(e):
                        raise
    return cache


def extract_bash_tools_from_messages(messages: list[Any]) -> set[str]:
    tools: set[str] = set()
    for message in messages:
        if getattr(message, "type", None) != "assistant":
            continue
        content = getattr(getattr(message, "message", None), "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == BASH_TOOL_NAME
            ):
                inp = block.get("input") or {}
                cmd = inp.get("command") if isinstance(inp, dict) else None
                name = _extract_cli_name(cmd if isinstance(cmd, str) else None)
                if name:
                    tools.add(name)
    return tools


_STRIPPED = frozenset({"sudo"})


def _extract_cli_name(command: str | None) -> str | None:
    if not command:
        return None
    for tok in command.strip().split():
        if re.match(r"^[A-Za-z_]\w*=", tok):
            continue
        if tok in _STRIPPED:
            continue
        return tok
    return None


# ============================================================================
# Content block inspection helpers
# ============================================================================


def _is_tool_use_block(block: Any) -> bool:
    """Check if a content block is a tool_use block."""
    return isinstance(block, dict) and block.get("type") == "tool_use"


def _is_tool_result_block(block: Any) -> bool:
    """Check if a content block is a tool_result block."""
    return isinstance(block, dict) and block.get("type") == "tool_result"


def _is_text_block(block: Any) -> bool:
    """Check if a content block is a text block."""
    return isinstance(block, dict) and block.get("type") == "text"


def _is_thinking_block(block: Any) -> bool:
    """Check if a content block is a thinking block."""
    return isinstance(block, dict) and block.get("type") in (
        "thinking",
        "redacted_thinking",
    )


def _get_message_content(message: Any) -> list[Any] | None:
    """Extract content blocks from a message, handling both Message objects and raw dicts."""
    if isinstance(message, dict):
        content = message.get("content") or message.get("message", {}).get("content")
    else:
        content = (
            getattr(getattr(message, "message", None), "content", None)
            if hasattr(message, "message")
            else getattr(message, "content", None)
        )
    if not isinstance(content, list):
        return None
    return content


# ============================================================================
# Message content extraction helpers
# ============================================================================


def get_assistant_text(message: Any) -> str:
    """Extract all text content from an assistant message.

    Joins text from all text blocks in the message content.  Returns an empty
    string when there is no assistant content available.

    Mirrors ``_assistant_text()`` in ``query/core.py`` but exposed as a public
    utility for use by hooks, compact strategies, and debug tooling.
    """
    content = _get_message_content(message)
    if content is None:
        if isinstance(message, dict):
            raw = message.get("content", "")
            if isinstance(raw, str):
                return raw
        return ""
    return " ".join(
        block.get("text", "")
        for block in content
        if _is_text_block(block)
    )


def get_last_assistant_text(messages: list[Any]) -> str | None:
    """Get the text content of the last assistant message in a conversation.

    Returns ``None`` if there are no assistant messages.
    """
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "assistant" or (
            isinstance(msg, dict) and msg.get("type") == "assistant"
        ):
            text = get_assistant_text(msg)
            if text:
                return text
    return None


def get_all_text_from_messages(messages: list[Any], *, role: str = "assistant") -> str:
    """Concatenate all text blocks from messages of a given role.

    Useful for building summaries from conversation history.
    """
    parts: list[str] = []
    for msg in messages:
        msg_type = (
            getattr(msg, "type", None)
            if not isinstance(msg, dict)
            else msg.get("type")
        )
        if msg_type != role:
            continue
        content = _get_message_content(msg)
        if content:
            for block in content:
                if _is_text_block(block):
                    parts.append(str(block.get("text", "")))
    return "\n".join(parts)


# ============================================================================
# Tool use / tool result extraction helpers
# ============================================================================


def get_tool_use_blocks(message: Any) -> list[dict[str, Any]]:
    """Extract all ``tool_use`` blocks from a single message.

    Handles both ``Message`` objects and raw dicts (stream events).
    """
    content = _get_message_content(message)
    if content is None:
        return []
    return [block for block in content if _is_tool_use_block(block)]


def get_all_tool_use_blocks(messages: list[Any]) -> list[dict[str, Any]]:
    """Collect every ``tool_use`` block from a list of messages.

    Only inspects assistant messages (the only role that emits tool-use blocks).
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        msg_type = (
            getattr(msg, "type", None)
            if not isinstance(msg, dict)
            else msg.get("type")
        )
        if msg_type != "assistant":
            continue
        out.extend(get_tool_use_blocks(msg))
    return out


def get_tool_result_blocks(message: Any) -> list[dict[str, Any]]:
    """Extract all ``tool_result`` blocks from a single user message."""
    content = _get_message_content(message)
    if content is None:
        return []
    return [block for block in content if _is_tool_result_block(block)]


def get_all_tool_results(messages: list[Any]) -> list[dict[str, Any]]:
    """Collect every ``tool_result`` block from user messages."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        msg_type = (
            getattr(msg, "type", None)
            if not isinstance(msg, dict)
            else msg.get("type")
        )
        if msg_type != "user":
            continue
        out.extend(get_tool_result_blocks(msg))
    return out


def get_unresolved_tool_use_ids(messages: list[Any]) -> set[str]:
    """Return the set of tool_use IDs that have no corresponding tool_result.

    Useful for detecting orphaned tool calls (e.g. after a streaming abort).
    """
    tool_use_ids: set[str] = set()
    resolved_ids: set[str] = set()

    for msg in messages:
        msg_type = (
            getattr(msg, "type", None)
            if not isinstance(msg, dict)
            else msg.get("type")
        )
        if msg_type == "assistant":
            for block in get_tool_use_blocks(msg):
                tid = block.get("id")
                if tid:
                    tool_use_ids.add(str(tid))
        elif msg_type == "user":
            for block in get_tool_result_blocks(msg):
                tid = block.get("tool_use_id")
                if tid:
                    resolved_ids.add(str(tid))

    return tool_use_ids - resolved_ids


def get_tool_use_blocks_by_name(
    messages: list[Any], tool_name: str
) -> list[dict[str, Any]]:
    """Return every ``tool_use`` block whose ``name`` matches *tool_name*."""
    return [
        block
        for block in get_all_tool_use_blocks(messages)
        if block.get("name") == tool_name
    ]


def get_tool_result_for_id(
    messages: list[Any], tool_use_id: str
) -> dict[str, Any] | None:
    """Find the ``tool_result`` block (if any) for a given tool_use ID."""
    for msg in messages:
        msg_type = (
            getattr(msg, "type", None)
            if not isinstance(msg, dict)
            else msg.get("type")
        )
        if msg_type != "user":
            continue
        for block in get_tool_result_blocks(msg):
            if str(block.get("tool_use_id", "")) == tool_use_id:
                return block
    return None


# ============================================================================
# Message-type checking predicates
# ============================================================================


def _msg_type(message: Any) -> str | None:
    """Extract the ``type`` field from a message, tolerating both objects and dicts."""
    if isinstance(message, dict):
        return message.get("type")
    return getattr(message, "type", None)


def is_assistant_message(message: Any) -> bool:
    """Return ``True`` when the message is an assistant message."""
    return _msg_type(message) == "assistant"


def is_user_message(message: Any) -> bool:
    """Return ``True`` when the message is a user message."""
    return _msg_type(message) == "user"


def is_system_message(message: Any) -> bool:
    """Return ``True`` when the message is a system message."""
    return _msg_type(message) == "system"


def is_attachment_message(message: Any) -> bool:
    """Return ``True`` when the message is an attachment message."""
    return _msg_type(message) == "attachment"


def is_progress_message(message: Any) -> bool:
    """Return ``True`` when the message is a progress message."""
    return _msg_type(message) == "progress"


def has_tool_use(message: Any) -> bool:
    """Return ``True`` when an assistant message carries at least one tool_use block."""
    return is_assistant_message(message) and len(get_tool_use_blocks(message)) > 0


def has_tool_result(message: Any) -> bool:
    """Return ``True`` when a user message carries at least one tool_result block."""
    return is_user_message(message) and len(get_tool_result_blocks(message)) > 0


# ============================================================================
# Stop-reason inspection helpers
# ============================================================================


def _get_stop_reason(message: Any) -> str | None:
    """Extract the stop_reason from a message, tolerating multiple shapes."""
    if isinstance(message, dict):
        msg = message.get("message", {})
        return (
            message.get("stop_reason")
            or msg.get("stop_reason")
            if isinstance(msg, dict)
            else None
        )
    return getattr(getattr(message, "message", None), "stop_reason", None)


def is_stop_reason_end_turn(message: Any) -> bool:
    """Return ``True`` when the assistant message finished naturally (end_turn)."""
    return is_assistant_message(message) and _get_stop_reason(message) == "end_turn"


def is_stop_reason_max_tokens(message: Any) -> bool:
    """Return ``True`` when the assistant message hit the token limit."""
    return is_assistant_message(message) and _get_stop_reason(message) == "max_tokens"


def is_stop_reason_tool_use(message: Any) -> bool:
    """Return ``True`` when the assistant message stopped to call tools."""
    return is_assistant_message(message) and _get_stop_reason(message) == "tool_use"


def is_stop_reason_error(message: Any) -> bool:
    """Return ``True`` when the assistant message reported an error stop reason."""
    return is_assistant_message(message) and _get_stop_reason(message) == "error"


# ============================================================================
# API error detection helpers
# ============================================================================


def get_api_error(message: Any) -> str | None:
    """Return the ``api_error`` field of an assistant message, or ``None``."""
    if isinstance(message, dict):
        return message.get("api_error")
    return getattr(message, "api_error", None)


def has_api_error(message: Any) -> bool:
    """Return ``True`` when the assistant message carries an API error."""
    if not is_assistant_message(message):
        return False
    err = get_api_error(message)
    if err is not None:
        return True
    if isinstance(message, dict):
        return bool(message.get("is_api_error_message"))
    return bool(getattr(message, "is_api_error_message", False))


def is_tool_result_error(block: dict[str, Any]) -> bool:
    """Return ``True`` when a tool_result block indicates an error."""
    if not _is_tool_result_block(block):
        return False
    return bool(block.get("is_error", False))


def count_tool_errors(messages: list[Any]) -> int:
    """Count how many tool_result blocks across user messages are errors."""
    count = 0
    for block in get_all_tool_results(messages):
        if is_tool_result_error(block):
            count += 1
    return count


# ============================================================================
# Conversation statistics / turn counting
# ============================================================================


def count_turns(messages: list[Any]) -> int:
    """Count the number of user → assistant turn pairs.

    A turn is counted each time we encounter an assistant message.  This
    matches the ``turn_count`` variable in the query loop (``query/core.py``).
    """
    return sum(1 for msg in messages if is_assistant_message(msg))


def count_turns_including_user(
    messages: list[Any], *, count_user_messages: bool = False
) -> int:
    """Count conversation turns, optionally counting user messages instead of assistant.

    When *count_user_messages* is ``False`` (the default), assistant messages
    are counted (matching ``count_turns``).  When ``True``, user messages
    that are not meta messages are counted instead.
    """
    if not count_user_messages:
        return count_turns(messages)

    def _is_meta(m: Any) -> bool:
        if isinstance(m, dict):
            return bool(m.get("is_meta", False))
        return bool(getattr(m, "is_meta", False))

    return sum(
        1
        for msg in messages
        if is_user_message(msg) and not _is_meta(msg)
    )


def count_tool_uses(messages: list[Any]) -> int:
    """Count total tool_use blocks across all assistant messages."""
    return len(get_all_tool_use_blocks(messages))


def count_tool_uses_by_name(messages: list[Any], tool_name: str) -> int:
    """Count tool_use blocks whose ``name`` matches *tool_name*."""
    return len(get_tool_use_blocks_by_name(messages, tool_name))


# ============================================================================
# Message filtering and lookup helpers
# ============================================================================


def filter_messages_by_type(
    messages: list[Any], message_type: str
) -> list[Any]:
    """Return all messages whose ``type`` matches *message_type*."""
    return [msg for msg in messages if _msg_type(msg) == message_type]


def find_message_by_uuid(
    messages: list[Any], uuid: str
) -> Any | None:
    """Find a message by its UUID.

    Returns ``None`` if no message matches.
    """
    for msg in messages:
        msg_uuid = (
            msg.get("uuid") if isinstance(msg, dict) else getattr(msg, "uuid", None)
        )
        if msg_uuid == uuid:
            return msg
    return None


def get_last_assistant_message(messages: list[Any]) -> Any | None:
    """Return the most recent assistant message, or ``None``."""
    for msg in reversed(messages):
        if is_assistant_message(msg):
            return msg
    return None


def get_last_non_meta_user_message(messages: list[Any]) -> Any | None:
    """Return the most recent non-meta user message, or ``None``."""

    def _is_meta(m: Any) -> bool:
        if isinstance(m, dict):
            return bool(m.get("is_meta", False))
        return bool(getattr(m, "is_meta", False))

    for msg in reversed(messages):
        if is_user_message(msg) and not _is_meta(msg):
            return msg
    return None


def filter_out_meta_messages(messages: list[Any]) -> list[Any]:
    """Return a copy of the message list with meta messages removed."""

    def _is_meta(m: Any) -> bool:
        if isinstance(m, dict):
            return bool(m.get("is_meta", False))
        return bool(getattr(m, "is_meta", False))

    return [msg for msg in messages if not _is_meta(msg)]


def get_messages_after_last_compact_boundary(
    messages: list[Any],
) -> list[Any]:
    """Return messages after the most recent compact boundary (system message).

    A compact boundary system message has subtype ``"compact_boundary"``.
    If no boundary is found, the entire list is returned.
    """
    boundary_idx = -1
    for i, msg in enumerate(messages):
        if is_system_message(msg):
            subtype = (
                msg.get("subtype", "")
                if isinstance(msg, dict)
                else getattr(msg, "subtype", "")
            )
            if subtype == "compact_boundary":
                boundary_idx = i
    if boundary_idx == -1:
        return list(messages)
    return list(messages[boundary_idx + 1:])


# ============================================================================
# Model string parsing and validation helpers
# ============================================================================

# Known Claude model prefixes for classification
_CLAUDE_MODEL_PREFIXES = (
    "claude-",
    "claude-sonnet-",
    "claude-opus-",
    "claude-haiku-",
)

# Models known to support extended-thinking
_THINKING_CAPABLE_PREFIXES = (
    "claude-sonnet-4-",
    "claude-opus-4-",
    "claude-3-5-sonnet-",
    "claude-3-7-sonnet-",
)


def parse_model_string(model: str) -> dict[str, str]:
    """Parse a model string into provider and family components.

    Returns a dict with keys ``"provider"`` and ``"family"``.

    >>> parse_model_string("claude-sonnet-4-20250514")
    {'provider': 'anthropic', 'family': 'sonnet-4'}
    """
    lower = model.lower().strip()
    result: dict[str, str] = {"provider": "", "family": ""}

    if lower.startswith("claude-"):
        result["provider"] = "anthropic"
    elif lower.startswith("gpt-") or lower.startswith("o1-") or lower.startswith("o3-"):
        result["provider"] = "openai"
    elif lower.startswith("gemini-"):
        result["provider"] = "google"
    else:
        result["provider"] = "unknown"

    # Extract family from known Claude patterns
    if lower.startswith("claude-sonnet-4-"):
        result["family"] = "sonnet-4"
    elif lower.startswith("claude-opus-4-"):
        result["family"] = "opus-4"
    elif lower.startswith("claude-haiku-4-"):
        result["family"] = "haiku-4"
    elif lower.startswith("claude-3-7-sonnet-"):
        result["family"] = "sonnet-3.7"
    elif lower.startswith("claude-3-5-sonnet-"):
        result["family"] = "sonnet-3.5"
    elif lower.startswith("claude-3-5-haiku-"):
        result["family"] = "haiku-3.5"
    elif lower.startswith("claude-3-opus-"):
        result["family"] = "opus-3"
    elif lower.startswith("claude-"):
        result["family"] = "legacy"

    return result


def is_claude_model(model: str) -> bool:
    """Return ``True`` when *model* looks like a Claude family model."""
    lower = model.lower().strip()
    return lower.startswith(_CLAUDE_MODEL_PREFIXES)


def is_valid_model_string(model: str) -> bool:
    """Heuristic validation that *model* has the shape of a real model identifier.

    A valid model string contains at least one ``-`` separator and at least
    5 characters total.
    """
    clean = model.strip()
    return len(clean) >= 5 and "-" in clean and not clean.startswith("-")


def model_supports_extended_thinking(model: str) -> bool:
    """Return ``True`` when *model* is known to support extended-thinking.

    Covers the current generation of Claude extended-thinking models.
    """
    lower = model.lower().strip()
    return lower.startswith(_THINKING_CAPABLE_PREFIXES)


def model_supports_prompt_caching(model: str) -> bool:
    """Return ``True`` when *model* supports prompt caching.

    All currently available Claude models support prompt caching, so this
    returns ``True`` for any recognized Claude model string.
    """
    return is_claude_model(model)


# Model-specific context window sizes (input tokens)
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "sonnet-4": 200_000,
    "opus-4": 200_000,
    "haiku-4": 200_000,
    "sonnet-3.7": 200_000,
    "sonnet-3.5": 200_000,
    "haiku-3.5": 200_000,
    "opus-3": 200_000,
}


def get_model_context_window(model: str) -> int:
    """Return the input context window size (in tokens) for a given model.

    Falls back to 200k for recognized Claude models and 128k for unknown models.
    """
    parsed = parse_model_string(model)
    family = parsed.get("family", "")
    if family in _MODEL_CONTEXT_WINDOWS:
        return _MODEL_CONTEXT_WINDOWS[family]
    if parsed.get("provider") == "anthropic":
        return 200_000
    return 128_000


def get_model_default_max_output_tokens(model: str) -> int:
    """Return the default max output tokens for a given model family.

    Claude Sonnet 4 models: 64k.  Older Claude models: 4096 (single pass default).
    """
    parsed = parse_model_string(model)
    family = parsed.get("family", "")
    if family in ("sonnet-4", "opus-4", "haiku-4"):
        return 64_000
    if parsed.get("provider") == "anthropic":
        return 4096
    return 4096


# ============================================================================
# Tool result content parsing
# ============================================================================


def parse_tool_result_content(block: dict[str, Any]) -> str:
    """Extract the text content from a tool_result block.

    Handles both string content (preferred) and list-of-blocks shapes.
    Returns an empty string when the content cannot be extracted.
    """
    if not _is_tool_result_block(block):
        return ""
    content = block.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content)


def get_tool_result_summary(content: str, max_len: int = 200) -> str:
    """Produce a truncated summary of a tool result string.

    When the content exceeds *max_len*, it is truncated and appended with
    ``…`` (ellipsis) and a character count.  Whitespace in the middle is
    collapsed so the summary is readable on one line.
    """
    if not content:
        return "(empty)"
    collapsed = " ".join(content.split())
    if len(collapsed) <= max_len:
        return collapsed
    return f"{collapsed[:max_len]}… ({len(collapsed)} chars)"


def format_tool_use_summary(block: dict[str, Any], *, max_input_len: int = 120) -> str:
    """Produce a human-readable one-line summary of a tool_use block.

    Example: ``"Bash(duration: 5s, description: ...)"`` for non-Bash tools,
    or ``"Bash(command)"`` for Bash tools.
    """
    name = block.get("name", "unknown")
    inp = block.get("input", {})
    if not isinstance(inp, dict):
        return f"{name}({max_input_len} chars input)"

    if name == "Bash":
        cmd = inp.get("command", "")
        if isinstance(cmd, str):
            collapsed = " ".join(cmd.split())
            if len(collapsed) <= max_input_len:
                return f"Bash({collapsed})"
            return f"Bash({collapsed[:max_input_len]}…)"
        return "Bash(...)"

    flat = ", ".join(
        f"{k}: {_summarize_value(v, max_input_len // max(1, len(inp)))}"
        for k, v in inp.items()
    )
    if len(flat) <= max_input_len:
        return f"{name}({flat})"
    return f"{name}({flat[:max_input_len]}…)"


def _summarize_value(value: Any, max_len: int) -> str:
    """Produce a short string representation of a tool input value."""
    s = str(value)
    collapsed = " ".join(s.split())
    if len(collapsed) <= max_len:
        return collapsed
    return f"{collapsed[:max_len]}…"


# ============================================================================
# Per-message token estimation helpers
# ============================================================================


def _estimate_string_tokens(s: str) -> int:
    """Rough token count: ~4 characters per token (for English text).

    This is an approximation only; for accurate counts use
    ``hare.utils.tokens.token_count_with_estimation``.
    """
    return max(1, len(s) // 4)


def estimate_message_tokens(message: Any) -> int:
    """Return a coarse per-message token estimate.

    Walks the message content (text, tool_use, tool_result) and sums
    character-count-based estimates.  For accurate counts, prefer
    ``token_count_with_estimation`` from ``hare.utils.tokens``.
    """
    content = _get_message_content(message)
    if content is None:
        raw = (
            message.get("content", "")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        )
        if isinstance(raw, str):
            return _estimate_string_tokens(raw)
        if isinstance(raw, list):
            content = raw
        else:
            return 0

    total = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            total += _estimate_string_tokens(str(block.get("text", "")))
        elif btype == "tool_use":
            inp = block.get("input", {})
            if isinstance(inp, dict):
                total += _estimate_string_tokens(str(inp))
        elif btype == "tool_result":
            c = block.get("content", "")
            total += _estimate_string_tokens(c if isinstance(c, str) else str(c))
    return total


def estimate_context_tokens_from_messages(messages: list[Any]) -> int:
    """Return a coarse token estimate for a list of messages.

    Uses per-message character-count heuristics.  For accurate counts
    prefer ``hare.utils.tokens.final_context_tokens_from_last_response``
    which reads the API-reported usage from the last assistant message.
    """
    return sum(estimate_message_tokens(msg) for msg in messages)


# ============================================================================
# Stream-event building helpers (SDK message shapes)
# ============================================================================


def build_stream_init_event(
    *,
    session_id: str,
    tools: list[str] | None = None,
    model: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a ``system/init`` stream event matching the SDK shape.

    This is the first event emitted by a query, establishing session metadata
    for downstream consumers.
    """
    event: dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "session_id": session_id,
    }
    if tools is not None:
        event["tools"] = tools
    if model is not None:
        event["model"] = model
    event.update(extra)
    return event


def build_stream_result_event(
    *,
    session_id: str,
    is_error: bool = False,
    result: str = "",
    duration_ms: float = 0.0,
    num_turns: int = 0,
    total_cost_usd: float = 0.0,
    stop_reason: str | None = None,
    uuid: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a ``result`` stream event matching the SDK shape.

    This is the terminal event emitted at the end of a query.
    """
    from uuid import uuid4 as _uuid4

    event: dict[str, Any] = {
        "type": "result",
        "subtype": "error" if is_error else "success",
        "is_error": is_error,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "session_id": session_id,
        "total_cost_usd": total_cost_usd,
        "uuid": uuid or str(_uuid4()),
    }
    if result:
        event["result"] = result
    if stop_reason:
        event["stop_reason"] = stop_reason
    event.update(extra)
    return event


def build_stream_error_event(
    *,
    session_id: str,
    error_type: str,
    is_error: bool = True,
    num_turns: int = 0,
    total_cost_usd: float = 0.0,
    duration_ms: float = 0.0,
    usage: dict[str, int] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build an error result event for cases like max_turns, max_budget, etc.

    The *error_type* becomes the ``subtype`` field, prefixed with ``"error_"``
    if not already prefixed.
    """
    from uuid import uuid4 as _uuid4

    subtype = error_type if error_type.startswith("error_") else f"error_{error_type}"
    event: dict[str, Any] = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "session_id": session_id,
        "total_cost_usd": total_cost_usd,
        "uuid": str(_uuid4()),
    }
    if usage is not None:
        event["usage"] = dict(usage)
    event.update(extra)
    return event


# ============================================================================
# Permission / tool-result triage helpers
# ============================================================================


def collect_permission_denials_from_results(
    messages: list[Any],
) -> list[dict[str, str]]:
    """Scan tool_results for permission-denied strings and return a summary.

    Each entry is a dict with ``"tool_use_id"`` and ``"reason"`` keys.
    """
    denials: list[dict[str, str]] = []
    _denial_pattern = re.compile(r"Permission denied", re.IGNORECASE)
    for block in get_all_tool_results(messages):
        if not is_tool_result_error(block):
            continue
        content = parse_tool_result_content(block)
        if _denial_pattern.search(content):
            denials.append(
                {
                    "tool_use_id": str(block.get("tool_use_id", "")),
                    "reason": content.strip(),
                }
            )
    return denials


def has_fatal_tool_error(messages: list[Any]) -> bool:
    """Return ``True`` if any tool_result block contains a fatal error marker.

    A fatal error is indicated by the string ``"FATAL:"`` appearing in the
    tool result content.
    """
    for block in get_all_tool_results(messages):
        if not is_tool_result_error(block):
            continue
        content = parse_tool_result_content(block)
        if "FATAL:" in content:
            return True
    return False


# ============================================================================
# Misc: message deduplication, action detection
# ============================================================================


def deduplicate_consecutive_user_messages(
    messages: list[Any],
) -> list[Any]:
    """Collapse consecutive user messages with identical content.

    When two back-to-back user messages have the same text content the
    second is dropped.  This is useful for cleaning up accidental double-sends
    in conversation history reconstruction.
    """
    if len(messages) < 2:
        return list(messages)

    out: list[Any] = [messages[0]]
    for msg in messages[1:]:
        prev = out[-1]
        if (
            is_user_message(msg)
            and is_user_message(prev)
            and _get_user_text(msg) == _get_user_text(prev)
        ):
            continue
        out.append(msg)
    return out


def was_tool_used_in_turn(
    messages: list[Any], tool_name: str, *, since_last_user: bool = True
) -> bool:
    """Check whether *tool_name* was invoked since the most recent user message.

    When *since_last_user* is ``False``, the entire message list is scanned.
    """
    if not since_last_user:
        return count_tool_uses_by_name(messages, tool_name) > 0

    # Walk backward until we hit the latest user message, then scan forward
    start_idx = 0
    for i in range(len(messages) - 1, -1, -1):
        if is_user_message(messages[i]):
            start_idx = i + 1
            if start_idx >= len(messages):
                return False
            break

    return any(
        block.get("name") == tool_name
        for block in get_all_tool_use_blocks(messages[start_idx:])
    )


def extract_thinking_content(message: Any) -> str:
    """Extract thinking / extended-thinking text from an assistant message.

    Returns the concatenated text of all thinking and redacted_thinking blocks.
    Returns an empty string when no thinking blocks are present.
    """
    content = _get_message_content(message)
    if content is None:
        return ""
    parts: list[str] = []
    for block in content:
        if _is_thinking_block(block):
            text = block.get("thinking", "") or block.get("text", "")
            if text:
                parts.append(str(text))
    return "\n".join(parts)


def has_thinking_content(message: Any) -> bool:
    """Return ``True`` when the assistant message includes thinking blocks."""
    return bool(extract_thinking_content(message))


def get_visible_message_types(messages: list[Any]) -> list[str]:
    """Return an ordered list of visible message types for UI rendering.

    Filters out progress and tombstone messages, keeping the user-visible
    conversation flow.
    """
    return [
        t
        for m in messages
        if (t := _msg_type(m)) is not None
        and t not in ("progress", "tombstone")
    ]


# ============================================================================
# Content-block constructors (useful for tests and synthetic message building)
# ============================================================================


def build_tool_use_block(
    tool_name: str,
    tool_use_id: str,
    input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a ``tool_use`` content block dict.

    >>> build_tool_use_block("Bash", "toolu_01", {"command": "ls"})
    {'type': 'tool_use', 'id': 'toolu_01', 'name': 'Bash', 'input': {'command': 'ls'}}
    """
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": tool_name,
        "input": input or {},
    }


def build_tool_result_block(
    tool_use_id: str,
    content: str | list[dict[str, Any]],
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    """Construct a ``tool_result`` content block dict.

    >>> build_tool_result_block("toolu_01", "file contents")
    {'type': 'tool_result', 'tool_use_id': 'toolu_01', 'content': 'file contents', 'is_error': False}
    """
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def build_text_block(text: str) -> dict[str, Any]:
    """Construct a ``text`` content block dict."""
    return {"type": "text", "text": text}


def build_thinking_block(thinking: str) -> dict[str, Any]:
    """Construct a ``thinking`` content block dict."""
    return {"type": "thinking", "thinking": thinking}


# ============================================================================
# Batch message processing helpers
# ============================================================================


async def normalize_messages_batch(
    messages: list[Any],
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield normalized SDK dicts for every message in *messages*.

    Thin convenience wrapper that chains :func:`normalize_message` calls so
    callers can write ``async for event in normalize_messages_batch(msgs)``.
    """
    for msg in messages:
        async for item in normalize_message(msg):
            yield item


# ============================================================================
# Message content inspection / emptiness checks
# ============================================================================


def _get_user_text(message: Any) -> str:
    """Extract all text content from a user message.

    Mirrors :func:`get_assistant_text` but for user messages — joins text
    from all text blocks.
    """
    content = _get_message_content(message)
    if content is None:
        if isinstance(message, dict):
            raw = message.get("content", "")
            if isinstance(raw, str):
                return raw
        return ""
    return " ".join(
        block.get("text", "")
        for block in content
        if _is_text_block(block)
    )


def is_empty_message(message: Any) -> bool:
    """Return ``True`` when a message has no meaningful text or tool content.

    An "empty" message is one where:
    - It is an assistant message with no text, thinking, or tool_use blocks.
    - It is a user message with no text or tool_result blocks.
    - It is a system message with no content or subtype.
    """
    msg_type = _msg_type(message)
    if msg_type == "assistant":
        content = _get_message_content(message)
        if not content:
            return True
        return not any(
            block.get("type") in ("text", "thinking", "redacted_thinking", "tool_use")
            for block in content
            if isinstance(block, dict)
        )
    if msg_type == "user":
        content = _get_message_content(message)
        if not content:
            raw = (
                message.get("content", "")
                if isinstance(message, dict)
                else getattr(message, "content", "")
            )
            if isinstance(raw, str):
                return not raw.strip()
            return True
        return not any(
            block.get("type") in ("text", "tool_result")
            for block in content
            if isinstance(block, dict)
        )
    if msg_type == "system":
        subtype = (
            message.get("subtype", "")
            if isinstance(message, dict)
            else getattr(message, "subtype", "")
        )
        content = (
            message.get("content", "")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        )
        return not subtype and not content
    if msg_type == "progress":
        return False  # progress messages carry tool execution data
    if msg_type == "attachment":
        attachment = (
            message.get("attachment", {})
            if isinstance(message, dict)
            else getattr(message, "attachment", {})
        )
        return not attachment
    return False


# ============================================================================
# Message timestamp helpers
# ============================================================================


def get_message_timestamp_iso(message: Any) -> str | None:
    """Extract the timestamp from a message and return it as an ISO-8601 string.

    Handles both string timestamps and numeric Unix timestamps (ms). Returns
    ``None`` when no timestamp is present or parsing fails.
    """
    ts: Any = None
    if isinstance(message, dict):
        ts = message.get("timestamp")
    else:
        ts = getattr(message, "timestamp", None)

    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000.0).isoformat()
        except (OSError, ValueError, OverflowError):
            return None
    return None


# ============================================================================
# Conversation result extraction
# ============================================================================


def extract_last_text_result(
    messages: list[Any],
    *,
    exclude_synthetic: bool = True,
) -> str | None:
    """Walk backwards through *messages* to find the last assistant text result.

    When *exclude_synthetic* is ``True`` (the default), assistant messages
    whose text matches known synthetic markers (e.g. interruption messages)
    are skipped.  Returns ``None`` when no text result is found.

    .. note::

       This is the extraction half of what ``query_engine.py`` does at the
       end of a query loop when building the ``result`` event.
    """
    _SYNTHETIC_TEXTS: frozenset[str] = frozenset({
        "[Request interrupted by user]",
        "[Request interrupted by user for tool use]",
        "No response requested.",
    })

    for msg in reversed(messages):
        if not is_assistant_message(msg):
            continue
        text = get_assistant_text(msg)
        if not text:
            continue
        if exclude_synthetic and text.strip() in _SYNTHETIC_TEXTS:
            continue
        return text
    return None


def extract_last_tool_use_block(
    messages: list[Any],
    tool_name: str | None = None,
) -> dict[str, Any] | None:
    """Find the most recent ``tool_use`` block, optionally filtered by name.

    Returns ``None`` when no matching block is found.
    """
    for msg in reversed(messages):
        if not is_assistant_message(msg):
            continue
        blocks = get_tool_use_blocks(msg)
        for block in reversed(blocks):
            if tool_name is None or block.get("name") == tool_name:
                return block
    return None


# ============================================================================
# Permission callback / approval-signature helpers
# ============================================================================


def build_permission_callback_event(
    tool_use_id: str,
    approved: bool,
    *,
    updated_input: dict[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Build a permission-callback payload matching the bridge protocol shape.

    This is the dict that gets sent back to the remote bridge when the user
    approves or denies a tool-use permission prompt.
    """
    event: dict[str, Any] = {
        "type": "permission_callback",
        "tool_use_id": tool_use_id,
        "approved": approved,
    }
    if updated_input is not None:
        event["updated_input"] = updated_input
    if reason:
        event["reason"] = reason
    return event


def has_any_pending_tool_calls(messages: list[Any]) -> bool:
    """Return ``True`` when there is at least one unresolved tool_use block.

    Convenience wrapper around :func:`get_unresolved_tool_use_ids`.
    """
    return len(get_unresolved_tool_use_ids(messages)) > 0


def count_pending_tool_calls(messages: list[Any]) -> int:
    """Return the number of unresolved tool_use blocks waiting for results."""
    return len(get_unresolved_tool_use_ids(messages))


# ============================================================================
# System reminder / content sanitization
# ============================================================================


def strip_system_reminders(text: str) -> str:
    """Remove all ``<system-reminder>...</system-reminder>`` tags from *text*.

    Returns the sanitized string with surrounding whitespace trimmed.
    """
    return _SYSTEM_REMINDER.sub("", text).strip()


def strip_line_number_prefixes(text: str) -> str:
    """Remove line-number prefixes from every line of *text*.

    Applies :func:`strip_line_number_prefix` to each line and re-joins with
    newlines.
    """
    return "\n".join(
        strip_line_number_prefix(line) for line in text.split("\n")
    )
