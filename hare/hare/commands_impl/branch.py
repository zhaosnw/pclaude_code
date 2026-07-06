"""
/branch command - fork (branch) the current conversation.

Port of: src/commands/branch/branch.ts + index.ts

Creates a fork of the current conversation by:
  1. Reading the current transcript file
  2. Creating a new session with a new UUID
  3. Copying all messages with preserved metadata
  4. Adding forkedFrom traceability
  5. Resuming into the fork
  6. Naming: "firstPrompt (Branch)", handling collisions → " (Branch 2)", etc.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

COMMAND_NAME = "branch"
DESCRIPTION = "Fork (branch) the current conversation into a new session"
ALIASES: list[str] = []


def derive_first_prompt(messages: list[dict[str, Any]]) -> str:
    """Derive a single-line title from the first user message."""
    for msg in messages:
        if msg.get("type") == "user":
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str):
                raw = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        raw = block.get("text", "")
                        break
                else:
                    raw = ""
            else:
                raw = str(content) if content else ""
            if raw:
                return re.sub(r"\s+", " ", raw).strip()[:100]
    return "Branched conversation"


async def _get_unique_fork_name(
    base_name: str,
    search_sessions_by_custom_title: Any = None,
) -> str:
    """Generate a unique fork name, handling collisions.

    If "baseName (Branch)" exists → "baseName (Branch 2)", etc.
    """
    candidate = f"{base_name} (Branch)"

    if search_sessions_by_custom_title:
        existing = await search_sessions_by_custom_title(candidate, exact=True)
        if not existing:
            return candidate

        # Find all existing forks to determine next number
        forks = await search_sessions_by_custom_title(f"{base_name} (Branch")
        used_numbers = {1}
        pattern = re.compile(rf"^{re.escape(base_name)} \(Branch(?: (\d+))?\)$")
        for session in forks:
            title = session.get("customTitle", "")
            match = pattern.match(title)
            if match:
                num = match.group(1)
                used_numbers.add(int(num) if num else 1)

        next_num = 2
        while next_num in used_numbers:
            next_num += 1
        return f"{base_name} (Branch {next_num})"

    return candidate


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Fork the current conversation into a new branch.

    The forked conversation preserves all original metadata and adds
    forkedFrom traceability to each message.
    """
    custom_title = args.strip() if args else None

    get_session_id = context.get("get_session_id")
    get_original_cwd = context.get("get_original_cwd")
    get_transcript_path = context.get("get_transcript_path")
    get_transcript_path_for_session = context.get("get_transcript_path_for_session")
    get_project_dir = context.get("get_project_dir")
    save_custom_title_fn = context.get("save_custom_title")
    search_sessions_by_custom_title = context.get("search_sessions_by_custom_title")
    resume_fn = context.get("resume")
    log_event = context.get("log_event")

    original_session_id = get_session_id() if get_session_id else str(uuid.uuid4())

    # Read current transcript
    transcript_path = get_transcript_path() if get_transcript_path else None
    if not transcript_path or not os.path.exists(transcript_path):
        return {"type": "text", "value": "No conversation to branch"}

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript_content = f.read()
    except Exception:
        return {"type": "text", "value": "No conversation to branch"}

    if not transcript_content.strip():
        return {"type": "text", "value": "No conversation to branch"}

    # Parse JSONL entries
    entries = []
    for line in transcript_content.strip().split("\n"):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Filter to main conversation messages (exclude sidechains)
    main_entries = [
        e
        for e in entries
        if e.get("type")
        in ("user", "assistant", "system", "progress", "tool_use", "tool_result")
        and not e.get("isSidechain")
    ]

    if not main_entries:
        return {"type": "text", "value": "No messages to branch"}

    # Create fork
    fork_session_id = str(uuid.uuid4())  # type: ignore[call-overload]
    project_dir = (
        get_project_dir(get_original_cwd())
        if get_project_dir and get_original_cwd
        else "."
    )
    fork_session_path = (
        get_transcript_path_for_session(fork_session_id)
        if get_transcript_path_for_session
        else os.path.join(project_dir, f"{fork_session_id}.jsonl")
    )

    if project_dir:
        os.makedirs(project_dir, exist_ok=True)

    parent_uuid = None
    lines: list[str] = []
    serialized_messages: list[dict[str, Any]] = []

    for entry in main_entries:
        forked_entry = {
            **entry,
            "sessionId": fork_session_id,
            "parentUuid": parent_uuid,
            "isSidechain": False,
            "forkedFrom": {
                "sessionId": original_session_id,
                "messageUuid": entry.get("uuid"),
            },
        }

        serialized = {**entry, "sessionId": fork_session_id}
        serialized_messages.append(serialized)
        lines.append(json.dumps(forked_entry))

        if entry.get("type") != "progress":
            parent_uuid = entry.get("uuid")

    # Write fork session file
    os.makedirs(os.path.dirname(fork_session_path), exist_ok=True)
    with open(fork_session_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(fork_session_path, 0o600)

    # Build session name
    first_prompt = derive_first_prompt(serialized_messages)
    base_name = custom_title or first_prompt
    effective_title = await _get_unique_fork_name(
        base_name, search_sessions_by_custom_title
    )

    if save_custom_title_fn:
        await save_custom_title_fn(fork_session_id, effective_title, fork_session_path)

    if log_event:
        log_event(
            "tengu_conversation_forked",
            {
                "message_count": len(serialized_messages),
                "has_custom_title": bool(custom_title),
            },
        )

    # Build LogOption for resume
    now = datetime.now(timezone.utc)
    fork_log = {
        "date": now.strftime("%Y-%m-%d"),
        "messages": serialized_messages,
        "fullPath": fork_session_path,
        "value": now.timestamp() * 1000,
        "created": now.isoformat(),
        "modified": now.isoformat(),
        "firstPrompt": first_prompt,
        "messageCount": len(serialized_messages),
        "isSidechain": False,
        "sessionId": fork_session_id,
        "customTitle": effective_title,
        "contentReplacements": [],
    }

    # Resume into the fork
    title_info = f' "{custom_title}"' if custom_title else ""
    resume_hint = f"\nTo resume the original: claude -r {original_session_id}"
    success_msg = (
        f"Branched conversation{title_info}. You are now in the branch.{resume_hint}"
    )

    if resume_fn:
        await resume_fn(fork_session_id, fork_log, "fork")
        return {"type": "text", "value": success_msg, "display": "system"}
    else:
        return {
            "type": "text",
            "value": f"Branched conversation{title_info}. Resume with: /resume {fork_session_id}",
        }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[title]",
        "call": call,
    }
