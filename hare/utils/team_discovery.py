"""Team discovery / teammate status (port of teamDiscovery.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hare.utils.swarm.backends.types import is_pane_backend
from hare.utils.swarm.team_helpers import read_team_file, sanitize_name

PaneBackendType = str


@dataclass
class TeammateStatus:
    name: str
    agent_id: str
    agent_type: str | None = None
    model: str | None = None
    prompt: str | None = None
    status: Literal["running", "idle", "unknown"] = "unknown"
    color: str | None = None
    idle_since: str | None = None
    tmux_pane_id: str = ""
    cwd: str = ""
    worktree_path: str | None = None
    is_hidden: bool | None = None
    backend_type: PaneBackendType | None = None
    mode: str | None = None


def get_teammate_statuses(team_name: str) -> list[TeammateStatus]:
    team_file = read_team_file(sanitize_name(team_name))
    if not team_file:
        return []

    hidden = set(team_file.get("hiddenPaneIds") or [])
    members = team_file.get("members") or []
    out: list[TeammateStatus] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        if member.get("name") == "team-lead":
            continue
        is_active = member.get("isActive", True) is not False
        status: Literal["running", "idle"] = "running" if is_active else "idle"
        pane_id = str(member.get("tmuxPaneId", ""))
        bt = member.get("backendType")
        out.append(
            TeammateStatus(
                name=str(member.get("name", "")),
                agent_id=str(member.get("agentId", "")),
                agent_type=member.get("agentType"),
                model=member.get("model"),
                prompt=member.get("prompt"),
                status=status,
                color=member.get("color"),
                tmux_pane_id=pane_id,
                cwd=str(member.get("cwd", "")),
                worktree_path=member.get("worktreePath"),
                is_hidden=pane_id in hidden,
                backend_type=bt if bt and is_pane_backend(bt) else None,
                mode=member.get("mode"),
            )
        )
    return out
