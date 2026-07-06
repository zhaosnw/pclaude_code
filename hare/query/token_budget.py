"""Token budget tracking for query continuations.

Port of: src/query/tokenBudget.ts (line-by-line).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Union

from hare.utils.token_budget import get_budget_continuation_message

# -- src/query/tokenBudget.ts L3-4
_COMPLETION_THRESHOLD = 0.9
_DIMINISHING_THRESHOLD = 500


# -- src/query/tokenBudget.ts L6-11
@dataclass
class BudgetTracker:
    continuation_count: int = 0
    last_delta_tokens: int = 0
    last_global_turn_tokens: int = 0
    started_at: float = field(default_factory=lambda: time.time() * 1000)


# -- src/query/tokenBudget.ts L13-20
def create_budget_tracker() -> BudgetTracker:
    return BudgetTracker(
        continuation_count=0,
        last_delta_tokens=0,
        last_global_turn_tokens=0,
        started_at=time.time() * 1000,
    )


# -- src/query/tokenBudget.ts L22-29
@dataclass
class ContinueDecision:
    nudge_message: str
    continuation_count: int
    pct: int
    turn_tokens: int
    budget: int
    action: str = "continue"


# -- src/query/tokenBudget.ts L31-41
@dataclass
class _StopCompletionEvent:
    continuation_count: int
    pct: int
    turn_tokens: int
    budget: int
    diminishing_returns: bool
    duration_ms: int


@dataclass
class StopDecision:
    completion_event: Optional[_StopCompletionEvent]
    action: str = "stop"


# -- src/query/tokenBudget.ts L43
TokenBudgetDecision = Union[ContinueDecision, StopDecision]


# -- src/query/tokenBudget.ts L45-93
def check_token_budget(
    tracker: BudgetTracker,
    agent_id: Optional[str],
    budget: Optional[int],
    global_turn_tokens: int,
) -> TokenBudgetDecision:
    if agent_id or budget is None or budget <= 0:
        return StopDecision(completion_event=None)

    turn_tokens = global_turn_tokens
    pct = round((turn_tokens / budget) * 100)
    delta_since_last_check = global_turn_tokens - tracker.last_global_turn_tokens

    is_diminishing = (
        tracker.continuation_count >= 3
        and delta_since_last_check < _DIMINISHING_THRESHOLD
        and tracker.last_delta_tokens < _DIMINISHING_THRESHOLD
    )

    if not is_diminishing and turn_tokens < budget * _COMPLETION_THRESHOLD:
        tracker.continuation_count += 1
        tracker.last_delta_tokens = delta_since_last_check
        tracker.last_global_turn_tokens = global_turn_tokens
        return ContinueDecision(
            nudge_message=get_budget_continuation_message(pct, turn_tokens, budget),
            continuation_count=tracker.continuation_count,
            pct=pct,
            turn_tokens=turn_tokens,
            budget=budget,
        )

    if is_diminishing or tracker.continuation_count > 0:
        return StopDecision(
            completion_event=_StopCompletionEvent(
                continuation_count=tracker.continuation_count,
                pct=pct,
                turn_tokens=turn_tokens,
                budget=budget,
                diminishing_returns=is_diminishing,
                duration_ms=int(time.time() * 1000 - tracker.started_at),
            ),
        )

    return StopDecision(completion_event=None)
