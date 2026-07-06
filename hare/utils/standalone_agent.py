"""Standalone agent name helpers (port of standaloneAgent.ts)."""

from __future__ import annotations

from typing import Any, Protocol


class _AppStateLike(Protocol):
    standalone_agent_context: dict[str, Any] | None


def get_standalone_agent_name(app_state: _AppStateLike) -> str | None:
    from hare.utils.teammate import get_team_name

    if get_team_name():
        return None
    ctx = app_state.standalone_agent_context
    if not ctx:
        return None
    return ctx.get("name") or None
