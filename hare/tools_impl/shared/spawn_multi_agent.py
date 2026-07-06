"""
Port of: src/tools/shared/spawnMultiAgent.ts (stub — original ties to React, tmux, swarm).

Teammate / multi-agent spawn orchestration. External UI and process APIs are stubbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class _GlobalConfig(Protocol):
    teammate_default_model: str | None


def resolve_teammate_model(input_model: str | None, leader_model: str | None) -> str:
    """Resolve 'inherit' and defaults for worker model (gh-31069)."""
    if input_model == "inherit":
        return leader_model or _default_teammate_model(leader_model)
    if input_model:
        return input_model
    return _default_teammate_model(leader_model)


def _default_teammate_model(leader_model: str | None) -> str:
    try:
        from hare.utils.config import get_global_config

        cfg = get_global_config()
        configured = getattr(cfg, "teammate_default_model", None)
    except Exception:
        configured = None
    if configured is None:
        return leader_model or "hare-3-5-sonnet-latest"
    return str(configured)


@dataclass
class SpawnOutput:
    teammate_id: str
    agent_id: str
    name: str
    tmux_session_name: str
    tmux_window_name: str
    tmux_pane_id: str
    agent_type: str | None = None
    model: str | None = None
    color: str | None = None
    team_name: str | None = None
    is_splitpane: bool | None = None
    plan_mode_required: bool | None = None


@dataclass
class SpawnMultiAgentOptions:
    """Placeholder for TS spawn options bag."""

    prompt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


async def spawn_teammate_async(_opts: SpawnMultiAgentOptions) -> SpawnOutput:
    """Stub: would spawn in-process or tmux teammate."""
    return SpawnOutput(
        teammate_id="stub",
        agent_id="stub-agent",
        name="stub",
        tmux_session_name="",
        tmux_window_name="",
        tmux_pane_id="",
    )
