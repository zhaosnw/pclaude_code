"""
TeamCreateTool – create a multi-agent swarm team.

Port of: src/tools/TeamCreateTool/TeamCreateTool.ts
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext
from hare.tools_impl.TeamCreateTool.prompt import get_prompt

TEAM_CREATE_TOOL_NAME = "TeamCreate"


@dataclass
class TeamFile:
    name: str
    description: str | None
    created_at: float
    lead_agent_id: str
    lead_session_id: str
    members: list[dict[str, Any]] = field(default_factory=list)


TEAM_LEAD_NAME = "team-lead"

_teams: dict[str, TeamFile] = {}


def sanitize_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())


def format_agent_id(name: str, team_name: str) -> str:
    return f"{name}@{team_name}"


class _TeamCreateTool(ToolBase):
    name = TEAM_CREATE_TOOL_NAME
    aliases: list[str] = []
    search_hint = "create a multi-agent swarm team"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "Name for the new team to create.",
                },
                "description": {
                    "type": "string",
                    "description": "Team description/purpose.",
                },
                "agent_type": {
                    "type": "string",
                    "description": "Type/role of the team lead.",
                },
            },
            "required": ["team_name"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def prompt(self, options: dict[str, Any]) -> str:
        return get_prompt()

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return "Create a new team for coordinating multiple agents"

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
        team_name = args.get("team_name", "")
        description = args.get("description")
        agent_type = args.get("agent_type", TEAM_LEAD_NAME)

        if not team_name or not team_name.strip():
            return ToolResult(data="Error: team_name is required", is_error=True)

        if team_name in _teams:
            import uuid

            team_name = f"team-{uuid.uuid4().hex[:8]}"

        lead_agent_id = format_agent_id(TEAM_LEAD_NAME, team_name)

        team_file = TeamFile(
            name=team_name,
            description=description,
            created_at=time.time(),
            lead_agent_id=lead_agent_id,
            lead_session_id="",
            members=[
                {
                    "agentId": lead_agent_id,
                    "name": TEAM_LEAD_NAME,
                    "agentType": agent_type,
                    "joinedAt": time.time(),
                }
            ],
        )
        _teams[team_name] = team_file

        import json

        result = {
            "team_name": team_name,
            "team_file_path": f"~/.hare/teams/{team_name}/config.json",
            "lead_agent_id": lead_agent_id,
        }
        return ToolResult(data=json.dumps(result, indent=2))


TeamCreateTool = _TeamCreateTool()
