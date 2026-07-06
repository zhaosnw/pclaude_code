"""
MCP skill builders – create skills from MCP server prompts.

Port of: src/skills/mcpSkillBuilders.ts
"""

from __future__ import annotations
from typing import Any
from hare.skills.loader import SkillDefinition


def build_mcp_skill(
    server_name: str,
    prompt_name: str,
    description: str = "",
    arguments: list[dict[str, Any]] | None = None,
) -> SkillDefinition:
    return SkillDefinition(
        name=f"{server_name}:{prompt_name}",
        description=description or f"MCP skill from {server_name}",
        source="mcp",
        type="prompt",
    )
