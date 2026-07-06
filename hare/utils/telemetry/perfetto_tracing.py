"""Port of: src/utils/telemetry/perfettoTracing.ts (stubs)"""

from __future__ import annotations

_enabled = False
_agents: set[str] = set()


def is_perfetto_tracing_enabled() -> bool:
    return _enabled


def register_agent(agent_id: str) -> None:
    _agents.add(agent_id)


def unregister_agent(agent_id: str) -> None:
    _agents.discard(agent_id)
