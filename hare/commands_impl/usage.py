"""
/usage command - show API usage statistics.

Port of: src/commands/usage/usage.tsx + index.ts

Shows token usage for the current session, including:
  - Input/output token counts
  - Cache hit/miss statistics
  - Cost estimate
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "usage"
DESCRIPTION = "Show API usage statistics"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show API usage for current session."""
    get_usage_stats = context.get("get_usage_stats")
    messages = context.get("messages", [])

    if get_usage_stats:
        stats = get_usage_stats()
    else:
        stats = _estimate_from_messages(messages)

    lines = [
        "## API Usage",
        "",
        f"**Input tokens:** {stats.get('input_tokens', 0):,}",
        f"**Output tokens:** {stats.get('output_tokens', 0):,}",
        f"**Total tokens:** {stats.get('total_tokens', 0):,}",
    ]

    cache_hits = stats.get("cache_hits", 0)
    cache_misses = stats.get("cache_misses", 0)
    if cache_hits or cache_misses:
        lines.append(f"**Cache hits:** {cache_hits:,}")
        lines.append(f"**Cache misses:** {cache_misses:,}")

    cost = stats.get("cost", 0)
    if cost:
        lines.append(f"**Estimated cost:** ${cost:.4f}")

    return {"type": "text", "value": "\n".join(lines)}


def _estimate_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate usage from message list."""
    input_tokens = 0
    output_tokens = 0
    for msg in messages:
        msg_type = msg.get("type", "")
        content = msg.get("message", {}).get("content", "")
        if isinstance(content, str):
            estimated = len(content) // 4
        elif isinstance(content, list):
            estimated = sum(
                len(b.get("text", "")) // 4
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            estimated = 0
        if msg_type in ("user", "system"):
            input_tokens += estimated
        else:
            output_tokens += estimated
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
