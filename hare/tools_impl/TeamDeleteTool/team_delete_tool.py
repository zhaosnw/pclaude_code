"""
TeamDeleteTool – disband a swarm team and clean up.

Port of: src/tools/TeamDeleteTool/TeamDeleteTool.ts
"""

from __future__ import annotations

from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext

TEAM_DELETE_TOOL_NAME = "TeamDelete"

TEAM_LEAD_NAME = "team-lead"


def get_prompt() -> str:
    return """# TeamDelete

Remove team and task directories when the swarm work is complete.

This operation:
- Removes the team directory (`~/.hare/teams/{team-name}/`)
- Removes the task directory (`~/.hare/tasks/{team-name}/`)
- Clears team context from the current session

**IMPORTANT**: TeamDelete will fail if the team still has active members.
Gracefully terminate teammates first, then call TeamDelete after all teammates have shut down."""


class _TeamDeleteTool(ToolBase):
    name = TEAM_DELETE_TOOL_NAME
    aliases: list[str] = []
    search_hint = "disband a swarm team and clean up"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def prompt(self, options: dict[str, Any]) -> str:
        return get_prompt()

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return "Clean up team and task directories when the swarm is complete"

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return ""

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        from hare.tools_impl.TeamCreateTool.team_create_tool import _teams

        team_name: str | None = None
        for name, tf in _teams.items():
            team_name = name
            break

        if team_name:
            tf = _teams.get(team_name)
            if tf:
                non_lead = [m for m in tf.members if m.get("name") != TEAM_LEAD_NAME]
                active = [m for m in non_lead if m.get("isActive") is not False]
                if active:
                    names = ", ".join(m.get("name", "") for m in active)
                    return ToolResult(
                        data=f"Cannot cleanup team with {len(active)} active member(s): {names}. "
                        "Use requestShutdown to gracefully terminate teammates first.",
                        is_error=True,
                    )
            _teams.pop(team_name, None)
            return ToolResult(
                data=f'Cleaned up directories and worktrees for team "{team_name}"'
            )

        return ToolResult(data="No team name found, nothing to clean up")


TeamDeleteTool = _TeamDeleteTool()
