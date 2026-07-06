"""Port of: src/commands/export.ts — Export the current conversation to a file."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

COMMAND_NAME = "export"
DESCRIPTION = "Export the current conversation to a file"
ALIASES: list[str] = []


def _content_of(msg: dict[str, Any]) -> str:
    body = msg.get("message", msg).get("content", "")
    if isinstance(body, list):
        return "\n\n".join(
            b.get("text", b.get("content", "")) if isinstance(b, dict) else str(b)
            for b in body
        )
    return str(body)


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Export conversation messages to JSON, Markdown, or plain text."""
    messages: list[dict[str, Any]] = (
        list(context.get("messages", [])) if isinstance(context, dict) else []
    )
    fmt, out_path = "json", "conversation.json"
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--format", "-f") and i + 1 < len(args):
            i += 1; fmt = args[i].lower()
        elif a.startswith("--format="):
            fmt = a.split("=", 1)[1].lower()
        elif a.startswith("--output=") or a.startswith("--path="):
            out_path = a.split("=", 1)[1]
        elif a in ("-o", "--output", "--path") and i + 1 < len(args):
            i += 1; out_path = args[i]
        elif not a.startswith("-"):
            out_path = a
        i += 1

    ext_map = {"json": ".json", "md": ".md", "markdown": ".md", "txt": ".txt", "text": ".txt"}
    ext = ext_map.get(fmt)
    if ext and not out_path.endswith(ext):
        out_path += ext
    out_path = os.path.expanduser(out_path)
    ts = datetime.now(timezone.utc).isoformat()

    try:
        if fmt in ("md", "markdown"):
            lines = [f"# Conversation Export\nExported: {ts}\nMessages: {len(messages)}\n"]
            for j, msg in enumerate(messages):
                role = msg.get("role", msg.get("type", "unknown"))
                lines.append(f"## {j + 1}. {role}\n\n{_content_of(msg)}\n\n---\n")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        elif fmt in ("txt", "text"):
            lines = [f"Conversation Export — {ts}", f"Messages: {len(messages)}", "=" * 60, ""]
            for j, msg in enumerate(messages):
                role = msg.get("role", msg.get("type", "unknown"))
                lines.append(f"[{j + 1}] {role}:\n{_content_of(msg)}\n{'-' * 40}\n")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        else:
            payload = {"exported_at": ts, "message_count": len(messages), "messages": messages}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)

        size = os.path.getsize(out_path)
        cwd = os.getcwd()
        rel = os.path.relpath(out_path, cwd) if out_path.startswith(cwd) else out_path
        return {"type": "text", "value": f"Exported {len(messages)} message(s) as {fmt.upper()} → {rel} ({size:,} bytes)"}
    except OSError as exc:
        return {"type": "text", "value": f"Export failed: {exc}"}
    except Exception as exc:
        return {"type": "text", "value": f"Unexpected error during export: {exc}"}
