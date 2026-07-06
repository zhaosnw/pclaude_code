"""Rendering helpers for subagent status. Port of: src/tools/AgentTool/agentDisplay.ts"""

from __future__ import annotations


def format_agent_title(name: str, task: str) -> str:
    return f"{name}: {task[:80]}"
