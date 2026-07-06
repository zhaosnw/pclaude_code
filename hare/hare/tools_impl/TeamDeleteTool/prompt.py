"""Port of: src/tools/TeamDeleteTool/prompt.ts"""

from __future__ import annotations


def get_prompt() -> str:
    return """# TeamDelete

Remove team and task directories when the swarm work is complete.

This operation:
- Removes the team directory (`~/.hare/teams/{team-name}/`)
- Removes the task directory (`~/.hare/tasks/{team-name}/`)
- Clears team context from the current session

**IMPORTANT**: TeamDelete will fail if the team still has active members. Gracefully terminate teammates first, then call TeamDelete after all teammates have shut down.

Use this when all teammates have finished their work and you want to clean up the team resources. The team name is automatically determined from the current session's team context."""
