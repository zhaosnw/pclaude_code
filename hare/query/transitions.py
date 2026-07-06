"""Query loop transition types.

Port of: src/query/transitions.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Optional

# Reasons the query loop may set on ``Continue`` before persisting to
# ``state.transition``. Unknown strings are coerced to ``next_turn`` so
# forward-compatible TS additions do not break Python tests or callbacks.
QUERY_LOOP_TRANSITION_REASONS: Final[frozenset[str]] = frozenset(
    {
        "next_turn",
        "max_output_tokens_escalate",
        "max_output_tokens_recovery",
        "stop_hook_blocking",
        "token_budget_continuation",
        "reactive_compact_retry",
        "collapse_drain_retry",
    }
)


def normalize_query_loop_transition(transition: Continue) -> Continue:
    """Return ``transition`` if its ``reason`` is whitelisted; else ``next_turn``."""
    if transition.reason in QUERY_LOOP_TRANSITION_REASONS:
        return transition
    return Continue(reason="next_turn")


@dataclass
class Terminal:
    reason: str = "completed"
    error: Optional[Any] = None
    turn_count: Optional[int] = None


@dataclass
class Continue:
    reason: str = "next_turn"
    attempt: Optional[int] = None
    committed: Optional[int] = None
