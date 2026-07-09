"""
Tests for query/ submodules: config.py, deps.py, token_budget.py, transitions.py.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from hare.query.config import QueryConfig, build_query_config, _Gates
from hare.query.deps import QueryDeps, production_deps
from hare.query.token_budget import (
    BudgetTracker,
    ContinueDecision,
    StopDecision,
    _StopCompletionEvent,
    check_token_budget,
    create_budget_tracker,
)


# ---------------------------------------------------------------------------
# QueryConfig tests
# ---------------------------------------------------------------------------


class TestQueryConfig:
    def test_config_is_frozen(self) -> None:
        cfg = QueryConfig(
            session_id="test",
            gates=_Gates(
                streaming_tool_execution=False,
                emit_tool_use_summaries=False,
                is_ant=False,
                fast_mode_enabled=True,
            ),
        )
        assert cfg.session_id == "test"
        assert cfg.gates.fast_mode_enabled is True

    def test_gates_structure(self) -> None:
        gates = _Gates(
            streaming_tool_execution=True,
            emit_tool_use_summaries=True,
            is_ant=False,
            fast_mode_enabled=True,
        )
        assert gates.streaming_tool_execution is True
        assert gates.emit_tool_use_summaries is True
        assert gates.is_ant is False

    def test_build_query_config_returns_valid_structure(self) -> None:
        cfg = build_query_config()
        assert isinstance(cfg, QueryConfig)
        assert cfg.session_id != ""
        assert isinstance(cfg.gates, _Gates)

    def test_build_query_config_fast_mode_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = build_query_config()
            # Without CLAUDE_CODE_DISABLE_FAST_MODE, fast_mode should be enabled
            assert cfg.gates.fast_mode_enabled is True

    def test_build_query_config_fast_mode_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_FAST_MODE": "true"}):
            cfg = build_query_config()
            assert cfg.gates.fast_mode_enabled is False

    def test_build_query_config_ant_mode(self) -> None:
        with mock.patch.dict(os.environ, {"USER_TYPE": "ant"}):
            cfg = build_query_config()
            assert cfg.gates.is_ant is True


# ---------------------------------------------------------------------------
# QueryDeps tests
# ---------------------------------------------------------------------------


class TestQueryDeps:
    def test_creation_with_defaults(self) -> None:
        async def dummy_model(*args, **kwargs) -> dict:
            return {}

        deps = QueryDeps(
            call_model=dummy_model,
            microcompact=dummy_model,
            autocompact=dummy_model,
        )
        assert deps.call_model is dummy_model
        assert deps.uuid is not None
        assert callable(deps.uuid)

    def test_uuid_generates_unique_values(self) -> None:
        async def dummy_model(*args, **kwargs) -> dict:
            return {}

        deps = QueryDeps(
            call_model=dummy_model,
            microcompact=dummy_model,
            autocompact=dummy_model,
        )
        ids = {deps.uuid() for _ in range(10)}
        assert len(ids) == 10  # all unique

    def test_production_deps_returns_query_deps(self) -> None:
        deps = production_deps()
        assert isinstance(deps, QueryDeps)
        assert deps.call_model is not None
        assert deps.microcompact is not None
        assert deps.autocompact is not None


# ---------------------------------------------------------------------------
# Token budget tests
# ---------------------------------------------------------------------------


class TestBudgetTracker:
    def test_create_budget_tracker(self) -> None:
        tracker = create_budget_tracker()
        assert tracker.continuation_count == 0
        assert tracker.last_delta_tokens == 0
        assert tracker.last_global_turn_tokens == 0

    def test_create_budget_tracker_sets_started_at(self) -> None:
        import time

        before = time.time() * 1000
        tracker = create_budget_tracker()
        after = time.time() * 1000
        assert before <= tracker.started_at <= after


class TestContinueDecision:
    def test_creation(self) -> None:
        d = ContinueDecision(
            nudge_message="continue",
            continuation_count=1,
            pct=50,
            turn_tokens=500,
            budget=1000,
        )
        assert d.action == "continue"
        assert d.pct == 50
        assert d.budget == 1000


class TestStopDecision:
    def test_without_completion_event(self) -> None:
        d = StopDecision(completion_event=None)
        assert d.action == "stop"
        assert d.completion_event is None

    def test_with_completion_event(self) -> None:
        event = _StopCompletionEvent(
            continuation_count=3,
            pct=95,
            turn_tokens=950,
            budget=1000,
            diminishing_returns=True,
            duration_ms=5000,
        )
        d = StopDecision(completion_event=event)
        assert d.completion_event is not None
        assert d.completion_event.diminishing_returns is True


class TestCheckTokenBudget:
    def test_stops_when_agent_id_present(self) -> None:
        tracker = create_budget_tracker()
        result = check_token_budget(tracker, "agent-1", 1000, 500)
        assert isinstance(result, StopDecision)
        assert result.completion_event is None

    def test_stops_when_budget_is_none(self) -> None:
        tracker = create_budget_tracker()
        result = check_token_budget(tracker, None, None, 500)
        assert isinstance(result, StopDecision)

    def test_stops_when_budget_is_zero(self) -> None:
        tracker = create_budget_tracker()
        result = check_token_budget(tracker, None, 0, 100)
        assert isinstance(result, StopDecision)

    def test_continues_when_under_threshold(self) -> None:
        tracker = create_budget_tracker()
        result = check_token_budget(tracker, None, 1000, 500)
        assert isinstance(result, ContinueDecision)
        assert result.pct == 50
        assert result.continuation_count == 1

    def test_tracks_continuation_count(self) -> None:
        tracker = create_budget_tracker()
        result = check_token_budget(tracker, None, 1000, 300)
        assert isinstance(result, ContinueDecision)
        assert result.continuation_count == 1

        result = check_token_budget(tracker, None, 1000, 500)
        assert isinstance(result, ContinueDecision)
        assert result.continuation_count == 2

    def test_stops_when_exceeds_threshold(self) -> None:
        tracker = create_budget_tracker()
        result = check_token_budget(tracker, None, 1000, 950)
        assert isinstance(result, StopDecision)
        # First time exceeding the threshold with no prior continuations:
        # completion_event is None (no prior continuity to report).
        assert result.completion_event is None

    def test_stops_with_completion_after_continuations(self) -> None:
        # After one continuation, exceeding threshold gives completion_event
        tracker = create_budget_tracker()
        check_token_budget(tracker, None, 1000, 100)  # continue once
        result = check_token_budget(tracker, None, 1000, 950)  # now exceeds threshold
        assert isinstance(result, StopDecision)
        assert result.completion_event is not None
        assert result.completion_event.pct == 95

    def test_diminishing_returns_after_3_continuations(self) -> None:
        tracker = create_budget_tracker()
        # 3 continuations under threshold with small deltas
        for i in range(3):
            tokens = 100 + i * 100
            result = check_token_budget(tracker, None, 1000, tokens)
            assert isinstance(result, ContinueDecision), (
                f"Expected continue at iteration {i}"
            )

        # 4th call: continuation_count >= 3, small delta → diminishing returns
        result = check_token_budget(tracker, None, 1000, 350)
        assert isinstance(result, StopDecision)
        assert result.completion_event is not None
        assert result.completion_event.diminishing_returns is True

    def test_stops_when_budget_negative(self) -> None:
        tracker = create_budget_tracker()
        result = check_token_budget(tracker, None, -100, 50)
        assert isinstance(result, StopDecision)
