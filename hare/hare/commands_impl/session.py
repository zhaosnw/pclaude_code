"""Port of: src/commands/session/. Show session info (id, duration, model, message count)."""

from __future__ import annotations

import time
from typing import Any

COMMAND_NAME = "session"
DESCRIPTION = "Show current session information (id, duration, model, message count)"
ALIASES: list[str] = ["info", "about"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Display session metadata: id, uptime, model, message and turn counts."""
    # Resolve context helpers
    get_session_id = context.get("get_session_id")
    get_app_state = context.get("get_app_state")
    get_usage_stats = context.get("get_usage_stats")
    options = context.get("options", {})

    session_id = get_session_id() if get_session_id else "unknown"
    model = options.get("mainLoopModel", options.get("model", "unknown"))
    session_start = context.get("session_start_time", None)

    lines: list[str] = [
        "## Session",
        "",
        f"**Session ID:** `{session_id}`",
    ]

    # Uptime
    if session_start:
        elapsed = int(time.time() - session_start)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            lines.append(f"**Uptime:** {hours}h {minutes}m {seconds}s")
        elif minutes:
            lines.append(f"**Uptime:** {minutes}m {seconds}s")
        else:
            lines.append(f"**Uptime:** {seconds}s")
    else:
        lines.append("**Uptime:** (tracking not available)")

    # Model
    lines.append(f"**Model:** {model}")

    # Token usage and message count
    if get_usage_stats:
        stats = get_usage_stats()
        input_tokens = stats.get("input_tokens", 0)
        output_tokens = stats.get("output_tokens", 0)
        message_count = stats.get("message_count", stats.get("turn_count", "unknown"))
        lines.append(f"**Messages / turns:** {message_count}")
        lines.append(
            f"**Tokens:** {input_tokens:,} in / {output_tokens:,} out "
            f"({input_tokens + output_tokens:,} total)"
        )
    else:
        lines.append("**Messages / turns:** (tracking not available)")
        lines.append("**Tokens:** (tracking not available)")

    # Remote session info
    if get_app_state:
        app_state = get_app_state()
        remote_url = app_state.get("remoteSessionUrl")
        if remote_url:
            lines.append("")
            lines.append("### Remote session")
            lines.append(f"**URL:** {remote_url}")
            lines.append("(Use `/session` in interactive mode for QR code)")
        else:
            lines.append("")
            lines.append("**Mode:** Local")
    else:
        lines.append("")
        lines.append("**Mode:** Local")

    return {"type": "text", "value": "\n".join(lines)}
