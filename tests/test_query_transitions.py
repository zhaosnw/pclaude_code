"""Unit tests for query transition helpers (parity with query loop callbacks)."""

from __future__ import annotations

import pytest

from hare.query.transitions import (
    QUERY_LOOP_TRANSITION_REASONS,
    Continue,
    normalize_query_loop_transition,
)

pytestmark = pytest.mark.alignment


def test_normalize_preserves_whitelisted_reason_and_identity() -> None:
    original = Continue(reason="token_budget_continuation", attempt=2, committed=1)
    out = normalize_query_loop_transition(original)
    assert out is original
    assert out.reason == "token_budget_continuation"
    assert out.attempt == 2
    assert out.committed == 1


def test_normalize_unknown_reason_becomes_next_turn_without_extra_fields() -> None:
    original = Continue(reason="hypothetical_future_ts_reason", attempt=9, committed=3)
    out = normalize_query_loop_transition(original)
    assert out is not original
    assert out.reason == "next_turn"
    assert out.attempt is None
    assert out.committed is None


def test_whitelist_covers_every_reason_emitted_by_query_loop() -> None:
    """If core.py introduces a new ``Continue(reason=...)`` site, extend the set."""
    assert QUERY_LOOP_TRANSITION_REASONS == frozenset(
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
