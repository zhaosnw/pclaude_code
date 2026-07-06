"""Status notice / token helpers for agent descriptions (port of statusNoticeHelpers.ts)."""

from __future__ import annotations

from typing import Any

AGENT_DESCRIPTIONS_THRESHOLD = 15_000


def get_agent_descriptions_total_tokens(
    agent_definitions: Any | None,
) -> int:
    if not agent_definitions:
        return 0
    try:
        from hare.services.token_estimation import rough_token_count_estimation
    except ImportError:

        def rough_token_count_estimation(s: str) -> int:  # type: ignore[misc]
            return max(1, len(s) // 4)

    total = 0
    active = getattr(agent_definitions, "active_agents", None) or []
    for agent in active:
        if getattr(agent, "source", None) == "built-in":
            continue
        desc = (
            f"{getattr(agent, 'agent_type', '')}: {getattr(agent, 'when_to_use', '')}"
        )
        total += rough_token_count_estimation(desc)
    return total
