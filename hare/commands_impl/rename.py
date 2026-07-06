"""Port of: src/commands/rename.ts — Rename the current conversation."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "rename"
DESCRIPTION = "Rename the current conversation"
ALIASES: list[str] = []


async def call(args: str, messages: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
    """Rename the session to a user-provided or AI-generated name."""
    name = args.strip()

    if not name:
        # Try to generate a name from conversation context
        generated = await _generate_session_name(messages, context)
        if generated:
            return {
                "type": "rename",
                "name": generated,
                "display_text": f"Conversation renamed to: {generated}",
            }
        return {"type": "error", "display_text": "Usage: /rename <name>"}

    return {
        "type": "rename",
        "name": name,
        "display_text": f"Conversation renamed to: {name}",
    }


async def _generate_session_name(
    messages: list[dict[str, Any]],
    context: dict[str, Any],
) -> str | None:
    """Generate a session name from the first meaningful user message."""
    # Extract the first non-meta user message
    for msg in messages:
        if isinstance(msg, dict):
            if msg.get("type") == "user":
                content = msg.get("message", {}).get("content", "")
                if not msg.get("is_meta"):
                    text = _extract_text(content)
                    if text and len(text) > 3:
                        # Use first sentence or first 60 chars
                        name = text.split(".")[0].strip()[:60]
                        if name:
                            return name

    # Fallback: current date
    from datetime import datetime
    return f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def _extract_text(content: Any) -> str:
    """Extract text from message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return " ".join(parts)
    return str(content) if content else ""


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argument_hint": "[name]",
    }
