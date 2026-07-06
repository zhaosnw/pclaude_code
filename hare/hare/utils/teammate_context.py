"""
Teammate context detection.

Port of: src/utils/teammateContext.ts
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class TeammateContext:
    agent_id: str
    name: str
    team_name: str


def get_teammate_context() -> TeammateContext | None:
    agent_id = os.environ.get("CLAUDE_CODE_AGENT_ID", "")
    if not agent_id:
        return None
    parts = agent_id.split("@", 1)
    name = parts[0]
    team_name = parts[1] if len(parts) > 1 else ""
    return TeammateContext(agent_id=agent_id, name=name, team_name=team_name)
