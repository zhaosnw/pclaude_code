"""Port of: src/commands/debug-tool-call/ — Debug info for the last tool call(s)."""

from __future__ import annotations

import json
from typing import Any

from hare.app_types.message import AssistantMessage, UserMessage

COMMAND_NAME = "debug-tool-call"
DESCRIPTION = "Show debug information for recent tool calls."
ALIASES: list[str] = []

# Content blocks that signal a tool-use in assistant messages
_TOOL_USE_TYPE = "tool_use"


def _extract_tool_uses(messages: list[Any]) -> list[dict[str, Any]]:
    """Walk messages in reverse and collect tool-use blocks from assistant messages."""
    tool_uses: list[dict[str, Any]] = []
    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage):
            content = msg.message.content if hasattr(msg.message, "content") else getattr(msg.message, "content", None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == _TOOL_USE_TYPE:
                        tool_uses.append({
                            "id": block.get("id", "?"),
                            "name": block.get("name", "?"),
                            "input": block.get("input", {}),
                            "message_uuid": getattr(msg, "uuid", "?"),
                            "duration_ms": getattr(msg, "duration_ms", None),
                            "cost_usd": getattr(msg, "cost_usd", 0.0),
                        })
            elif isinstance(content, str) and content:
                # String content may contain tool_use markers
                pass
        elif isinstance(msg, UserMessage):
            # Check for tool results fed back into user messages
            if msg.tool_use_result:
                # Link result to the preceding tool call if possible
                pass
    # Return in chronological order (oldest first)
    tool_uses.reverse()
    return tool_uses


def _format_input(input_data: Any, max_len: int = 200) -> str:
    """Pretty-print tool input, truncating large values."""
    if isinstance(input_data, dict):
        parts: list[str] = []
        for k, v in input_data.items():
            s = json.dumps(v, ensure_ascii=False)
            if len(s) > max_len:
                s = s[:max_len] + "..."
            parts.append(f"    {k}: {s}")
        return "\n".join(parts) if parts else "    (empty)"
    if isinstance(input_data, str):
        return input_data[:max_len] + ("..." if len(input_data) > max_len else "")
    return json.dumps(input_data, ensure_ascii=False)[:max_len]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show debug information for recent tool calls in the conversation.

    Without arguments, shows the last 5 tool calls. Pass a number to show more
    (e.g. /debug-tool-call 10). Pass --full or -f to show full input/output.
    """
    # Extract messages from context
    if isinstance(context, dict):
        messages: list[Any] = context.get("messages", [])
    else:
        messages = getattr(context, "messages", []) if hasattr(context, "messages") else []

    if any(a in ("--help", "-h") for a in args):
        return {"type": "text", "value": (
            "Usage: /debug-tool-call [count] [options]\n\n"
            "Show debug information for recent tool calls.\n\n"
            "Options:\n"
            "  --full, -f    Show full input/output (no truncation)\n"
            "  --json, -j    Output as JSON\n"
            "  --help, -h    Show this help\n\n"
            "Examples:\n"
            "  /debug-tool-call            Show last 5 tool calls\n"
            "  /debug-tool-call 10 -f      Show last 10 with full details"
        )}

    limit = 5
    full = False
    as_json = False
    for a in args:
        stripped = a.strip()
        if stripped.isdigit():
            limit = int(stripped)
        elif stripped in ("--full", "-f"):
            full = True
        elif stripped in ("--json", "-j"):
            as_json = True

    tool_uses = _extract_tool_uses(messages)

    if not tool_uses:
        return {"type": "text", "value": "No tool calls found in conversation."}

    # Take the last N
    selected = tool_uses[-limit:] if len(tool_uses) > limit else tool_uses

    if as_json:
        return {"type": "text", "value": json.dumps(selected, indent=2, ensure_ascii=False)}

    lines: list[str] = [
        f"Tool calls in conversation: {len(tool_uses)} total, showing last {len(selected)}",
        "",
    ]
    for i, tu in enumerate(selected, 1):
        lines.append(f"[{i}] {tu['name']}  (id: {tu['id']})")
        max_input = 99999 if full else 200
        lines.append(_format_input(tu["input"], max_input))
        extra: list[str] = []
        if tu["duration_ms"] is not None:
            extra.append(f"{tu['duration_ms']:.0f}ms")
        if tu["cost_usd"]:
            extra.append(f"${tu['cost_usd']:.6f}")
        if extra:
            lines.append(f"    -- {', '.join(extra)}")
        lines.append("")

    return {"type": "text", "value": "\n".join(lines)}
