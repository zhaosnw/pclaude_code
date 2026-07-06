"""Load and deserialize transcripts for resume (`conversationRecovery.ts`)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from hare.utils.messages import (
    NO_RESPONSE_REQUESTED,
    filter_orphaned_thinking_only_messages,
    filter_unresolved_tool_uses,
    filter_whitespace_only_assistant_messages,
)

TurnInterruptionKind = Literal["none", "interrupted_prompt", "interrupted_turn"]


def deserialize_message(entry: Any) -> Any:
    """Hydrate one transcript-envelope dict into a live Message dataclass so the
    object-oriented query loop can consume a resumed conversation. Already-object
    inputs pass through; unknown entry types return None (dropped by the caller).
    """
    from uuid import uuid4

    from hare.app_types.message import (
        APIMessage,
        AssistantMessage,
        AttachmentMessage,
        ProgressMessage,
        SystemMessage,
        UserMessage,
    )

    if not isinstance(entry, dict):
        return entry

    t = entry.get("type")
    uuid = entry.get("uuid") or str(uuid4())
    ts = entry.get("timestamp", "") or ""
    raw = entry.get("message") or {}

    def _api(default_role: str) -> "APIMessage":
        return APIMessage(
            role=raw.get("role", default_role),
            content=raw.get("content", ""),
            id=raw.get("id"),
        )

    if t == "user":
        return UserMessage(
            uuid=uuid,
            timestamp=ts,
            message=_api("user"),
            is_meta=bool(entry.get("isMeta", False)),
            is_compact_summary=bool(entry.get("isCompactSummary", False)),
        )
    if t == "assistant":
        return AssistantMessage(
            uuid=uuid,
            timestamp=ts,
            message=_api("assistant"),
            is_api_error_message=bool(entry.get("isApiErrorMessage", False)),
        )
    if t == "system":
        content = entry.get("content")
        return SystemMessage(
            uuid=uuid,
            timestamp=ts,
            subtype=entry.get("subtype", ""),
            content=content if isinstance(content, str) else "",
        )
    if t == "attachment":
        return AttachmentMessage(
            uuid=uuid, timestamp=ts, attachment=entry.get("attachment") or {}
        )
    if t == "progress":
        return ProgressMessage(
            uuid=uuid,
            timestamp=ts,
            tool_use_id=entry.get("toolUseId") or entry.get("tool_use_id", ""),
        )
    return None


def deserialize_messages(
    serialized_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    r = deserialize_messages_with_interrupt_detection(serialized_messages)
    return r["messages"]


def deserialize_messages_with_interrupt_detection(
    serialized_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    migrated = [_migrate_legacy_attachment_types(m) for m in serialized_messages]
    filtered = filter_unresolved_tool_uses(migrated)
    filtered = filter_orphaned_thinking_only_messages(filtered)
    filtered = filter_whitespace_only_assistant_messages(filtered)

    state = _detect_turn_interruption(filtered)
    if state["kind"] == "interrupted_turn":
        continuation = _create_user_message(
            "Continue from where you left off.",
            is_meta=True,
        )
        filtered.append(continuation)
        turn_interruption_state: dict[str, Any] = {
            "kind": "interrupted_prompt",
            "message": continuation,
        }
    else:
        turn_interruption_state = state

    last_relevant_idx = -1
    for idx in range(len(filtered) - 1, -1, -1):
        t = filtered[idx].get("type")
        if t not in ("system", "progress"):
            last_relevant_idx = idx
            break
    if last_relevant_idx != -1 and filtered[last_relevant_idx].get("type") == "user":
        filtered.insert(
            last_relevant_idx + 1,
            _create_assistant_message(NO_RESPONSE_REQUESTED),
        )

    return {
        "messages": filtered,
        "turnInterruptionState": turn_interruption_state,
    }


async def load_conversation_for_resume(
    source: str | dict[str, Any] | None,
    source_jsonl_file: str | None = None,
) -> dict[str, Any] | None:
    log: dict[str, Any] | None = None
    messages: list[dict[str, Any]] | None = None
    session_id: str | None = None

    if source is None:
        log = _load_latest_session_log()
    elif source_jsonl_file:
        loaded = await load_messages_from_jsonl_path(source_jsonl_file)
        messages = loaded["messages"]
        session_id = loaded["session_id"]
    elif isinstance(source, str):
        session_id = source
        log = _load_session_log_by_id(source)
    else:
        log = source

    if log is None and messages is None:
        return None

    if log is not None:
        if _is_lite_log(log):
            full = _load_full_log_from_entry(log)
            if full is not None:
                log = full
        if session_id is None:
            session_id = _get_session_id_from_log(log)
        messages = list(log.get("messages") or [])

    if messages is None:
        return None

    restore_skill_state_from_messages(messages)
    deserialized = deserialize_messages_with_interrupt_detection(messages)
    messages = list(deserialized["messages"])

    # Process session start hooks for resume (mirrors TS: 'resume' + { sessionId })
    try:
        from hare.utils.session_start import process_session_start_hooks

        hook_messages = await process_session_start_hooks(
            "resume", {"sessionId": session_id}
        )
        if isinstance(hook_messages, list):
            messages.extend(
                m for m in hook_messages if isinstance(m, dict) and m.get("type")
            )
    except Exception:
        pass

    # Copy plan and file history for resume (try/except — modules may not exist)
    if session_id:
        try:
            from hare.utils.plans import copy_plan_for_resume

            copy_plan_for_resume(log, session_id)
        except Exception:
            pass
        try:
            from hare.utils.file_history import copy_file_history_for_resume

            copy_file_history_for_resume(log)
        except Exception:
            pass

    # Hydrate transcript-envelope dicts into live Message objects so the
    # object-oriented query loop can consume the resumed conversation (it does
    # attribute access like msg.type). Internal dict-based processing above
    # (filters, interrupt detection, hooks) is complete by this point.
    hydrated = [deserialize_message(m) for m in messages]
    messages = [m for m in hydrated if m is not None]

    return {
        "messages": messages,
        "turnInterruptionState": deserialized["turnInterruptionState"],
        "fileHistorySnapshots": (log or {}).get("fileHistorySnapshots"),
        "attributionSnapshots": (log or {}).get("attributionSnapshots"),
        "contentReplacements": (log or {}).get("contentReplacements"),
        "contextCollapseCommits": (log or {}).get("contextCollapseCommits"),
        "contextCollapseSnapshot": (log or {}).get("contextCollapseSnapshot"),
        "sessionId": session_id,
        "agentName": (log or {}).get("agentName"),
        "agentColor": (log or {}).get("agentColor"),
        "agentSetting": (log or {}).get("agentSetting"),
        "customTitle": (log or {}).get("customTitle"),
        "tag": (log or {}).get("tag"),
        "mode": (log or {}).get("mode"),
        "worktreeSession": (log or {}).get("worktreeSession"),
        "prNumber": (log or {}).get("prNumber"),
        "prUrl": (log or {}).get("prUrl"),
        "prRepository": (log or {}).get("prRepository"),
        "fullPath": (log or {}).get("fullPath"),
    }


async def load_messages_from_jsonl_path(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"messages": [], "session_id": None}
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    log = _extract_log_from_rows(rows)
    return {
        "messages": list((log or {}).get("messages") or []),
        "session_id": _get_session_id_from_log(log or {}),
    }


def restore_skill_state_from_messages(messages: list[dict[str, Any]]) -> None:
    for msg in messages:
        if msg.get("type") != "attachment":
            continue
        att = msg.get("attachment") or {}
        if att.get("type") == "invoked_skills":
            try:
                from hare.bootstrap.state import add_invoked_skill  # type: ignore[import-not-found]

                for sk in att.get("skills") or []:
                    name, path, content = (
                        sk.get("name"),
                        sk.get("path"),
                        sk.get("content"),
                    )
                    if name and path and content:
                        add_invoked_skill(name, path, content, None)
            except ImportError:
                pass
        if att.get("type") == "skill_listing":
            try:
                from hare.utils.attachments import suppress_next_skill_listing

                suppress_next_skill_listing()
            except Exception:
                pass


def _migrate_legacy_attachment_types(message: dict[str, Any]) -> dict[str, Any]:
    if message.get("type") != "attachment":
        return message
    attachment = message.get("attachment")
    if not isinstance(attachment, dict):
        return message

    if attachment.get("type") == "new_file":
        filename = attachment.get("filename")
        if isinstance(filename, str):
            return {
                **message,
                "attachment": {
                    **attachment,
                    "type": "file",
                    "displayPath": _display_path(filename),
                },
            }

    if attachment.get("type") == "new_directory":
        path = attachment.get("path")
        if isinstance(path, str):
            return {
                **message,
                "attachment": {
                    **attachment,
                    "type": "directory",
                    "displayPath": _display_path(path),
                },
            }

    if "displayPath" not in attachment:
        p = (
            attachment.get("filename")
            or attachment.get("path")
            or attachment.get("skillDir")
        )
        if isinstance(p, str):
            return {
                **message,
                "attachment": {
                    **attachment,
                    "displayPath": _display_path(p),
                },
            }
    return message


def _display_path(path: str) -> str:
    try:
        from hare.utils.cwd import get_cwd

        cwd = get_cwd()
        return os.path.relpath(path, cwd)
    except Exception:
        return path


def _detect_turn_interruption(messages: list[dict[str, Any]]) -> dict[str, Any]:
    if not messages:
        return {"kind": "none"}

    last: dict[str, Any] | None = None
    for m in reversed(messages):
        t = m.get("type")
        is_api_err = t == "assistant" and m.get("isApiErrorMessage")
        if t not in ("system", "progress") and not is_api_err:
            last = m
            break
    if last is None:
        return {"kind": "none"}

    t = last.get("type")
    if t == "assistant":
        return {"kind": "none"}
    if t == "user":
        if last.get("isMeta") or last.get("isCompactSummary"):
            return {"kind": "none"}
        if _is_terminal_tool_result(last):
            return {"kind": "none"}
        if _is_tool_use_result_message(last):
            return {"kind": "interrupted_turn"}
        return {"kind": "interrupted_prompt", "message": last}
    if t == "attachment":
        return {"kind": "interrupted_turn"}
    return {"kind": "none"}


def _is_tool_use_result_message(message: dict[str, Any]) -> bool:
    if message.get("type") != "user":
        return False
    content = (message.get("message") or {}).get("content")
    if not isinstance(content, list):
        return False
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            return True
    return False


# TS `isTerminalToolResult` — brief-mode tool results signal that the turn
# ended cleanly (not interrupted). Without this check, brief-mode sessions
# are mis-classified as "interrupted_turn" on resume, producing a phantom
# "Continue where you left off" message.
BRIEF_TOOL_NAME = "Brief"
LEGACY_BRIEF_TOOL_NAME = "SendBrief"
SEND_USER_FILE_TOOL_NAME = "SendUserFile"


def _is_terminal_tool_result(message: dict[str, Any]) -> bool:
    """Check if a user message contains a tool_result from a brief/terminal tool.

    Mirrors TS isTerminalToolResult: tool_results from BRIEF_TOOL_NAME,
    LEGACY_BRIEF_TOOL_NAME, or SEND_USER_FILE_TOOL_NAME indicate the turn
    ended cleanly (brief mode), not an interruption.
    """
    if message.get("type") != "user":
        return False
    content = (message.get("message") or {}).get("content")
    if not isinstance(content, list):
        return False
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            tool_name = b.get("tool_name", "")
            if tool_name in (
                BRIEF_TOOL_NAME,
                LEGACY_BRIEF_TOOL_NAME,
                SEND_USER_FILE_TOOL_NAME,
            ):
                return True
    return False


def _create_user_message(content: str, *, is_meta: bool) -> dict[str, Any]:
    return {
        "type": "user",
        "isMeta": is_meta,
        "message": {"role": "user", "content": content},
    }


def _create_assistant_message(content: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": content}],
        },
    }


def _load_latest_session_log() -> dict[str, Any] | None:
    base = _transcript_base()
    if not base.exists():
        return None
    files = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        loaded = _load_log_from_path(p)
        if loaded is not None:
            return loaded
    return None


def _load_session_log_by_id(session_id: str) -> dict[str, Any] | None:
    try:
        from hare.utils.session_storage import get_transcript_path

        return _load_log_from_path(Path(get_transcript_path(session_id)))
    except Exception:
        return None


def _load_log_from_path(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return _extract_log_from_rows(rows)


def _extract_log_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(rows):
        if isinstance(row.get("messages"), list):
            return row
    if rows:
        # A plain transcript file is a stream of message entries with no wrapper
        # row. Exclude sidechain (sub-agent) entries, then walk the parentUuid
        # chain from the latest non-sidechain leaf so resumed conversations don't
        # leak sub-agent internals to the main model context. Matches TS
        # getLastSessionLog (findLatestMessage m => !m.isSidechain +
        # buildConversationChain).
        main_rows = [r for r in rows if not r.get("isSidechain")]
        if not main_rows:
            main_rows = rows  # fallback: all-sidechain file
        index: dict[str, dict[str, Any]] = {}
        for r in main_rows:
            uid = r.get("uuid")
            if isinstance(uid, str) and uid:
                index[uid] = r
        # Find the latest non-system-non-progress leaf — the deepest node in
        # the parentUuid chain. If one has children, others should too.
        leaf = None
        for r in reversed(main_rows):
            if r.get("type") not in ("user", "assistant"):
                continue
            leaf = r
            break
        if leaf and index:
            from hare.utils.session_storage import build_conversation_chain

            chain = build_conversation_chain(index, leaf)
        else:
            chain = list(main_rows)
        # Lift sessionId off the entries
        log: dict[str, Any] = {"messages": chain}
        for row in main_rows:
            sid = row.get("sessionId")
            if isinstance(sid, str) and sid:
                log["sessionId"] = sid
                break
        return log
    return None


def _is_lite_log(log: dict[str, Any]) -> bool:
    try:
        from hare.utils.session_storage import is_lite_log

        return bool(is_lite_log(log))
    except Exception:
        return False


def _load_full_log_from_entry(log: dict[str, Any]) -> dict[str, Any] | None:
    session_id = _get_session_id_from_log(log)
    if not session_id:
        return None
    try:
        from hare.utils.session_storage import load_full_log

        full_rows = load_full_log(session_id)
    except Exception:
        return None
    if isinstance(full_rows, list):
        extracted = _extract_log_from_rows(full_rows)
        if extracted is not None:
            return extracted
    return None


def _get_session_id_from_log(log: dict[str, Any]) -> str | None:
    for key in ("sessionId", "session_id", "id"):
        val = log.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _transcript_base() -> Path:
    return Path.home() / ".hare" / "transcripts"
