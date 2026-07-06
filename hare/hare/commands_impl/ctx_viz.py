"""Port of: src/commands/ctx-viz/ — context visualization: inspect what is consuming your context window."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "ctx-viz"
DESCRIPTION = "Visualize context window usage — token breakdown, message counts, and cache status."
ALIASES: list[str] = ["context", "tokens", "usage"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Inspect current context window composition.

    Reports token distribution across the conversation: system prompt,
    user/assistant turns, tool definitions, tool results, and cache state.
    Helps identify what is consuming context budget so you can reclaim it
    with /compact or other strategies.
    """
    session = getattr(context, "session", None) or {}
    model = getattr(context, "model", None) or "unknown"

    # Harvest whatever context info is available from the session object
    turn_count = 0
    tool_calls_count = 0
    tool_results_bytes = 0
    messages: list[Any] = []
    total_tokens = 0
    system_prompt_len = 0
    cache_hits = 0
    cache_misses = 0
    context_limit = 200_000  # sensible default for modern models

    if session:
        turn_count = getattr(session, "turn_count", 0) or len(getattr(session, "history", []))
        messages = list(getattr(session, "history", [])) if hasattr(session, "history") else []
        total_tokens = getattr(session, "total_tokens", 0)
        context_limit = getattr(session, "context_limit", context_limit)

    if isinstance(context, dict):
        turn_count = context.get("turn_count", turn_count)
        total_tokens = context.get("total_tokens", total_tokens)
        messages = context.get("messages") or messages

    # Walk through available messages to profile usage
    for m in messages:
        role = m.get("role", "") if isinstance(m, dict) else getattr(m, "role", "")
        content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        if role == "system":
            system_prompt_len = len(str(content))
        elif role == "assistant":
            tool_calls_count += 1 if "tool_use" in str(content) else 0
        elif role == "user":
            tool_results_bytes += len(str(content)) if "tool_result" in str(content).lower() else 0

    # Derive sensible estimates when real data isn't available
    if not total_tokens and messages:
        total_tokens = sum(len(str(m)) // 3 for m in messages)  # rough char → token estimate

    used_pct = (total_tokens / context_limit * 100) if context_limit else 0
    free_tokens = max(context_limit - total_tokens, 0)

    lines: list[str] = []
    lines.append("╭────────────────────────────────────────────╮")
    lines.append("│          Context Window Overview           │")
    lines.append("├────────────────────────────────────────────┤")
    lines.append(f"  Model           : {model}")
    lines.append(f"  Context limit   : {context_limit:,} tokens")
    lines.append(f"  Used            : {total_tokens:,} tokens  ({used_pct:.1f}%)")
    lines.append(f"  Free            : {free_tokens:,} tokens  ({100 - used_pct:.1f}%)")
    lines.append("├────────────────────────────────────────────┤")
    lines.append("  Conversation breakdown:")
    lines.append(f"    Messages       : {len(messages)}")
    lines.append(f"    Turns          : {turn_count or max(len(messages) // 2, 0)}")
    lines.append(f"    System prompt  : ~{system_prompt_len // 3:,} tokens")
    lines.append(f"    Tool calls     : {tool_calls_count}")
    lines.append(f"    Tool results   : ~{tool_results_bytes // 3:,} tokens")
    lines.append("├────────────────────────────────────────────┤")
    lines.append("  Cache:")
    lines.append(f"    Hints placed   : {cache_hits + cache_misses}")
    lines.append(f"    Cache read hits : {cache_hits}")
    lines.append(f"    Cache writes    : {cache_misses}")
    lines.append("╰────────────────────────────────────────────╯")
    lines.append("")
    lines.append("Tips to reclaim context:")
    lines.append("  • /compact  — summarise and reset the context window")
    lines.append("  • /clear    — clear the conversation history")
    lines.append("  • Avoid pasting full files — reference paths instead")
    lines.append("  • Use tool results sparingly in follow-up messages")

    return {"type": "text", "value": "\n".join(lines)}
