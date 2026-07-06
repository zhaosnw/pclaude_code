"""
/think-back command - replay or inspect past reasoning blocks.

Port of: src/commands/thinkback/thinkback.tsx + index.ts

Replays thinking blocks from past assistant messages.
In the TS CLI this is an interactive Ink component.
In the headless SDK, it lists available thinking blocks.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "think-back"
DESCRIPTION = "Replay or inspect past reasoning (thinking blocks)"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """List or replay thinking blocks from past messages."""
    messages = context.get("messages", [])
    get_thinking_blocks = context.get("get_thinking_blocks")

    arg = (args or "").strip()

    # Extract thinking blocks from message history
    thinking_blocks = []
    if get_thinking_blocks:
        thinking_blocks = await get_thinking_blocks()
    else:
        for i, msg in enumerate(messages):
            if msg.get("type") == "assistant":
                content = msg.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "thinking":
                            thinking_blocks.append(
                                {
                                    "index": i,
                                    "message_index": i,
                                    "thinking": block.get("thinking", "")[:200],
                                }
                            )

    if not thinking_blocks:
        return {
            "type": "text",
            "value": "No thinking blocks found in the current conversation.\n\nThinking blocks are available when using extended thinking models.",
        }

    # If arg is a number, show that specific block
    if arg and arg.isdigit():
        idx = int(arg)
        if 0 <= idx < len(thinking_blocks):
            block = thinking_blocks[idx]
            return {
                "type": "text",
                "value": f"## Thinking Block {idx}\n\n```\n{block['thinking']}\n```",
            }
        return {
            "type": "text",
            "value": f"Invalid index {idx}. Available: 0-{len(thinking_blocks) - 1}",
        }

    # List available blocks
    lines = [f"## Thinking Blocks ({len(thinking_blocks)})", ""]
    for i, block in enumerate(thinking_blocks):
        preview = block.get("thinking", "")[:100].replace("\n", " ")
        lines.append(f"**[{i}]** {preview}...")

    lines.extend(["", "Use `/think-back <index>` to view a specific block."])
    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[index]",
        "call": call,
    }
