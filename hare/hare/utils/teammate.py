"""
Teammate detection and info.

Port of: src/utils/teammate.ts
"""

from __future__ import annotations

import os


def is_teammate() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_AGENT_ID"))


def get_agent_name() -> str | None:
    agent_id = os.environ.get("CLAUDE_CODE_AGENT_ID", "")
    if not agent_id:
        return None
    parts = agent_id.split("@")
    return parts[0] if parts else None


def get_team_name() -> str | None:
    agent_id = os.environ.get("CLAUDE_CODE_AGENT_ID", "")
    if not agent_id:
        return None
    parts = agent_id.split("@")
    return parts[1] if len(parts) > 1 else None
