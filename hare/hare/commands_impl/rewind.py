"""Port of: src/commands/rewind.ts. Rewind the conversation by removing recent messages."""

from __future__ import annotations
from typing import Any

COMMAND_NAME = "rewind"
DESCRIPTION = "Rewind the conversation to a previous turn"
ALIASES: list[str] = ["undo", "rollback", "back"]


def _preview(msg: dict[str, Any], max_len: int = 80) -> str:
    c = msg.get("content", "")
    if isinstance(c, str):
        return c[:max_len].replace("\n", " ") + ("..." if len(c) > max_len else "")
    if isinstance(c, list):
        return "[multi-part content]"
    return str(c)[:max_len]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Rewind the conversation or show recent turns available for rewind.

    Without arguments, lists recent turns. With a numeric count, removes that
    many messages. Supports --dry-run/-n and --list/-l flags.
    """
    if isinstance(context, dict):
        messages: list[dict[str, Any]] = context.get("messages", [])
        set_messages = context.get("set_messages")
    else:
        messages = getattr(context, "messages", [])
        set_messages = getattr(context, "set_messages", None)
    if any(a in ("--help", "-h") for a in args):
        return {"type": "text", "value": (
            "Usage: /rewind [count] [options]\n\n"
            "Rewind the conversation by removing recent messages.\n\n"
            "Options:\n"
            "  --list, -l     List recent turns (default when no count given)\n"
            "  --dry-run, -n  Preview what would be removed without removing\n"
            "  --help, -h     Show this help\n\n"
            "Examples:\n"
            "  /rewind          Show available turns\n"
            "  /rewind 3        Remove last 3 messages\n"
            "  /rewind 5 -n     Preview removing 5 messages"
        )}
    if not messages:
        return {"type": "text", "value": "No messages in conversation to rewind."}
    numeric = [int(a.strip()) for a in args
               if a.strip().lstrip("-").isdigit() and not a.startswith("--")]
    count = numeric[0] if numeric else 0
    list_only = any(a in ("--list", "-l") for a in args)
    dry_run = any(a in ("--dry-run", "-n") for a in args)
    # List mode (default when no count given)
    if list_only or count <= 0:
        n = min(10, len(messages))
        recent = messages[-n:] if len(messages) > n else messages
        lines = [f"Recent turns ({len(messages)} total, showing last {len(recent)}):", ""]
        for i, msg in enumerate(recent):
            idx = len(messages) - len(recent) + i + 1
            lines.append(f"  [{idx}] ({msg.get('role', 'unknown')}) {_preview(msg)}")
        lines.extend(["", "To rewind: /rewind <count>", "Example: /rewind 3"])
        return {"type": "text", "value": "\n".join(lines)}
    # Clamp and execute (or dry-run)
    count = max(1, min(count, len(messages)))
    removed = messages[-count:] if count < len(messages) else messages
    new_messages = messages[:-count] if count < len(messages) else []
    if dry_run:
        lines = [f"[DRY RUN] Would remove {count} message(s):", ""]
        for i, msg in enumerate(removed):
            lines.append(f"  [{i + 1}] ({msg.get('role', 'unknown')}) {_preview(msg, 100)}")
        lines.extend(["", f"After rewind: {len(new_messages)} message(s) would remain.",
                       "", f"Use /rewind {count} to execute."])
        return {"type": "text", "value": "\n".join(lines)}
    if set_messages and callable(set_messages):
        set_messages(new_messages)
    return {
        "type": "rewind",
        "messages_removed": count,
        "new_messages": new_messages,
        "display_text": f"Rewound {count} message(s). {len(new_messages)} remaining.",
    }
