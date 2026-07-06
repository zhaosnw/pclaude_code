"""
Agent ID formatting.

Port of: src/utils/agentId.ts
"""

from __future__ import annotations


def format_agent_id(name: str, team_name: str) -> str:
    return f"{name}@{team_name}"


def parse_agent_id(agent_id: str) -> tuple[str, str]:
    """Returns (name, team_name)."""
    parts = agent_id.split("@", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return agent_id, ""
