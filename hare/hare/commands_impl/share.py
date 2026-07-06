"""Port of: src/commands/share/ — Share session conversation."""

from __future__ import annotations

import os
import datetime
from typing import Any

COMMAND_NAME = "share"
DESCRIPTION = "Share the current session conversation"
ALIASES: list[str] = []


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Share the session or export transcript to a file (--export <path>)."""
    if "--export" in args:
        idx = args.index("--export")
        path = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        export_path = os.path.abspath(os.path.expanduser(
            path or os.path.join(os.getcwd(), "hare-conversation-export.txt")
        ))
        lines = [
            "=" * 60,
            "Hare Session Export",
            f"Exported: {datetime.datetime.now().isoformat()}",
            "=" * 60,
        ]
        try:
            session = context.session
            sid = getattr(session, "id", getattr(session, "session_id", "unknown"))
            model = getattr(session, "model", getattr(session, "model_name", "unknown"))
            msg_count = getattr(session, "message_count", 0)
            lines += [
                f"Session ID: {sid}",
                f"Model: {model}",
                f"Message count: {msg_count}",
            ]
            for msg in getattr(session, "history", getattr(session, "messages", [])):
                role = getattr(msg, "role", "")
                content = getattr(msg, "content", str(msg))
                lines.append(f"\n[{role.upper()}]: {content}")
        except Exception:
            lines.append("\n(No active session context available)")
        try:
            os.makedirs(os.path.dirname(export_path) or ".", exist_ok=True)
            with open(export_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            size_kb = os.path.getsize(export_path) / 1024.0
            return {
                "type": "text",
                "value": f"Exported to `{export_path}` ({size_kb:.1f} KB).",
            }
        except OSError as exc:
            return {"type": "text", "value": f"Export failed: {exc}"}

    return {
        "type": "text",
        "value": (
            "Share this session:\n"
            "\n"
            "  Web UI  — copy the browser URL to share a snapshot.\n"
            "  Export  — `/share --export <path>` saves transcript to a file.\n"
            "  Resume  — share the session ID so others can `--resume` it.\n"
        ),
    }
