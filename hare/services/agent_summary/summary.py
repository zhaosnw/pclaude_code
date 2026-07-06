"""Port of: src/services/AgentSummary/"""

from __future__ import annotations
from typing import Any


async def generate_agent_summary(
    messages: list[dict[str, Any]], agent_type: str = ""
) -> str:
    """Generate a summary of an agent's work (stub)."""
    tool_count = 0
    for m in messages:
        c = m.get("message", {}).get("content", [])
        if isinstance(c, list):
            tool_count += sum(
                1 for b in c if isinstance(b, dict) and b.get("type") == "tool_use"
            )
    return f"Agent ({agent_type}) completed with {len(messages)} messages and {tool_count} tool uses."
