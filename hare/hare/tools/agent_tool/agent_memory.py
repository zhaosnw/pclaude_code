"""Agent memory path detection — port of src/tools/AgentTool/agentMemory.ts."""

from __future__ import annotations


def is_agent_memory_path(path: str) -> bool:
    """Check whether a file path belongs to agent memory storage.

    Agent memory is stored under ~/.claude/agent-memory/ (or ~/.hare/).
    Paths under this directory should not be auto-edited or treated as
    user project files.
    """
    import os

    home = os.path.expanduser("~")
    return path.startswith(
        os.path.join(home, ".claude", "agent-memory")
    ) or path.startswith(os.path.join(home, ".hare", "agent-memory"))
