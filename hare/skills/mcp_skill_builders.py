"""Port of: src/skills/mcpSkillBuilders.ts"""

from __future__ import annotations
from typing import Any


def build_mcp_skill(server_name: str, tool_name: str) -> dict[str, Any]:
    return {
        "name": f"mcp-{server_name}-{tool_name}",
        "type": "mcp",
        "server": server_name,
        "tool": tool_name,
    }
