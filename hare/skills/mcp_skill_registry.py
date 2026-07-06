"""
MCP skill builder registry (port of src/skills/mcpSkillBuilders.ts).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class McpSkillBuilders:
    create_skill_command: Callable[..., Any]
    parse_skill_frontmatter_fields: Callable[..., Any]


_builders: McpSkillBuilders | None = None


def register_mcp_skill_builders(b: McpSkillBuilders) -> None:
    global _builders
    _builders = b


def get_mcp_skill_builders() -> McpSkillBuilders:
    if _builders is None:
        raise RuntimeError(
            "MCP skill builders not registered — load_skills_dir has not been evaluated"
        )
    return _builders
