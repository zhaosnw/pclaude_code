"""Port of: src/commands/summary/. Summarize the current conversation session."""

from __future__ import annotations

import re
import time
from typing import Any

from hare.services.token_estimation import estimate_tokens

COMMAND_NAME = "summary"
DESCRIPTION = "Summarize the current conversation"
ALIASES: list[str] = []


def _extract_key_topics(messages: list[dict[str, Any]], top_n: int = 5) -> list[str]:
    """Extract candidate topic phrases from user messages using simple heuristics."""
    user_texts: list[str] = []
    for msg in messages:
        if msg.get("type") != "user":
            continue
        content = msg.get("message", {}).get("content", "")
        if isinstance(content, str):
            user_texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    user_texts.append(block.get("text", ""))

    combined = " ".join(user_texts)
    # Extract sentences or clauses as topic candidates
    sentences = re.split(r"[.!?\n]+", combined)
    candidates = [s.strip() for s in sentences if len(s.strip()) > 10]
    # Deduplicate while preserving order, take top N
    seen: set[str] = set()
    topics: list[str] = []
    for c in candidates:
        lower = c.lower()
        if lower not in seen:
            seen.add(lower)
            topics.append(c)
        if len(topics) >= top_n:
            break
    return topics


def _extract_files(messages: list[dict[str, Any]]) -> list[str]:
    """Extract file paths referenced in conversation messages."""
    files: set[str] = set()
    file_pattern = re.compile(r"[\w./\\-]+\.\w{1,10}")
    for msg in messages:
        content = msg.get("message", {}).get("content", "")
        if isinstance(content, str):
            for match in file_pattern.finditer(content):
                path = match.group()
                if "/" in path or "\\" in path or "." in path:
                    files.add(path)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "") or block.get("content", "")
                    if isinstance(text, str):
                        for match in file_pattern.finditer(text):
                            path = match.group()
                            if "/" in path or "\\" in path or "." in path:
                                files.add(path)
    return sorted(files)[:20]


async def call(args: str, messages: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
    """Summarize the current conversation: topics, stats, files, and metadata."""
    args_stripped = args.strip()

    # --- Basic counts ---
    total_msgs = len(messages)
    user_msgs = sum(1 for m in messages if m.get("type") == "user")
    assistant_msgs = sum(1 for m in messages if m.get("type") == "assistant")

    tool_uses = 0
    for msg in messages:
        content = msg.get("message", {}).get("content", [])
        if isinstance(content, list):
            tool_uses += sum(
                1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
            )

    # --- Token estimate ---
    all_text = ""
    for msg in messages:
        c = msg.get("message", {}).get("content", "")
        if isinstance(c, str):
            all_text += c
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "text":
                    all_text += b.get("text", "")
    est_tokens = estimate_tokens(all_text)

    # --- Model and session ---
    get_session_id = context.get("get_session_id")
    options = context.get("options", {})
    model = options.get("mainLoopModel", "unknown")
    session_id = get_session_id() if get_session_id else "local"

    # --- Topics ---
    topics = _extract_key_topics(messages)

    # --- Files ---
    referenced_files = _extract_files(messages)

    # --- Build output ---
    lines: list[str] = []
    lines.append("## Conversation Summary")
    lines.append("")
    lines.append(f"**Session:** `{session_id}`")
    lines.append(f"**Model:** {model}")
    lines.append(f"**Messages:** {total_msgs} ({user_msgs} user, {assistant_msgs} assistant)")
    lines.append(f"**Tool uses:** {tool_uses}")
    lines.append(f"**Estimated tokens:** {est_tokens:,}")

    if topics:
        lines.append("")
        lines.append("### Key Topics")
        for i, topic in enumerate(topics, 1):
            lines.append(f"{i}. {topic}")

    if referenced_files:
        lines.append("")
        lines.append("### Files Referenced")
        for f in referenced_files:
            lines.append(f"- `{f}`")

    if args_stripped:
        lines.append("")
        lines.append(f"*Filter: \"{args_stripped}\"*")
        lines.append("*Detailed topic filtering is not yet implemented in headless mode.*")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
