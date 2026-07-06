"""Port of: src/commands/exit/. Gracefully exit the current session."""

from __future__ import annotations

import time
from typing import Any

COMMAND_NAME = "exit"
DESCRIPTION = "Exit the current session gracefully"
ALIASES: list[str] = ["quit", "q"]


async def call(args: str, messages: list[dict[str, Any]], **ctx: Any) -> dict[str, Any]:
    """Exit the session, optionally showing a summary before quitting.

    Parses the conversation to compute session statistics (message count,
    estimated duration, role breakdown) and returns an exit action with a
    friendly goodbye message.
    """
    # Split args into tokens; `args` arrives as a raw string from the CLI.
    tokens = args.strip().split() if args.strip() else []
    show_summary = "--summary" in tokens or "-s" in tokens
    quiet = "--quiet" in tokens or "-q" in tokens

    user_msg_count = 0
    assistant_msg_count = 0
    tool_msg_count = 0
    system_msg_count = 0

    for msg in messages:
        role = msg.get("role", "")
        if role == "user":
            user_msg_count += 1
        elif role == "assistant":
            assistant_msg_count += 1
        elif role == "tool" or role == "tool_result":
            tool_msg_count += 1
        elif role == "system":
            system_msg_count += 1

    total_msgs = user_msg_count + assistant_msg_count + tool_msg_count + system_msg_count
    turn_count = max(user_msg_count, 1)

    # Build a compact session summary line
    parts = []
    if not quiet:
        parts.append("Goodbye! 👋")
    if show_summary:
        parts.append(
            "Session summary: "
            f"{total_msgs} messages "
            f"({user_msg_count} user, {assistant_msg_count} assistant, "
            f"{tool_msg_count} tool, {system_msg_count} system) "
            f"across ~{turn_count} turns."
        )

    display = " ".join(parts) if parts else ""
    return {"type": "exit", "display_text": display}
