"""
Unit tests for hare.cost_tracker — cost and usage tracking.

Port of: src/services/api/usage.ts + src/cost-tracker.ts behavior verification.
"""

from __future__ import annotations

from hare.cost_tracker import (
    CostTracker,
    add_api_duration,
    add_usage,
    format_total_cost,
    get_cost_summary,
    get_model_usage,
    get_total_api_duration,
    get_total_cost,
    reset_cost_tracker,
    save_current_session_costs,
)
from hare.services.api.logging import NonNullableUsage


def _make_usage(input_tokens: int = 1000, output_tokens: int = 500) -> NonNullableUsage:
    return NonNullableUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


# ---------------------------------------------------------------------------
# CostTracker dataclass
# ---------------------------------------------------------------------------


def test_cost_tracker_defaults() -> None:
    ct = CostTracker()
    assert ct.total_input_tokens == 0
    assert ct.total_output_tokens == 0
    assert ct.total_cache_creation_tokens == 0
    assert ct.total_cache_read_tokens == 0
    assert ct.total_api_duration == 0.0
    assert ct.total_cost_usd == 0.0
    assert ct.request_count == 0


# ---------------------------------------------------------------------------
# add_usage
# ---------------------------------------------------------------------------


def test_add_usage_increments_counters() -> None:
    reset_cost_tracker()
    add_usage(_make_usage(input_tokens=2000, output_tokens=1000))
    summary = get_cost_summary()
    assert summary["input_tokens"] == 2000
    assert summary["output_tokens"] == 1000
    assert summary["request_count"] == 1
    assert summary["total_cost_usd"] > 0  # Should have calculated cost


def test_add_usage_accumulates() -> None:
    reset_cost_tracker()
    add_usage(_make_usage(input_tokens=100, output_tokens=50))
    add_usage(_make_usage(input_tokens=200, output_tokens=100))
    summary = get_cost_summary()
    assert summary["input_tokens"] == 300
    assert summary["output_tokens"] == 150
    assert summary["request_count"] == 2


def test_add_usage_cost_defaults_to_sonnet_pricing() -> None:
    reset_cost_tracker()
    # 1M input tokens at $3 = $0.003 per 1K → 1000 tokens = $0.003
    # 1M output tokens at $15 = $0.015 per 1K → 500 tokens = $0.0075
    # Total ≈ $0.0105
    add_usage(_make_usage(input_tokens=1000, output_tokens=500))
    cost = get_total_cost()
    assert 0.01 <= cost <= 0.011  # ~$0.0105


# ---------------------------------------------------------------------------
# add_api_duration
# ---------------------------------------------------------------------------


def test_add_api_duration() -> None:
    reset_cost_tracker()
    add_api_duration(1.5)
    add_api_duration(0.5)
    assert get_total_api_duration() == 2.0


# ---------------------------------------------------------------------------
# get_cost_summary
# ---------------------------------------------------------------------------


def test_get_cost_summary_structure() -> None:
    reset_cost_tracker()
    add_usage(_make_usage())
    summary = get_cost_summary()
    assert "input_tokens" in summary
    assert "output_tokens" in summary
    assert "cache_creation_tokens" in summary
    assert "cache_read_tokens" in summary
    assert "total_cost_usd" in summary
    assert "total_api_duration" in summary
    assert "request_count" in summary


# ---------------------------------------------------------------------------
# get_model_usage
# ---------------------------------------------------------------------------


def test_get_model_usage_returns_dict() -> None:
    result = get_model_usage()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# reset_cost_tracker
# ---------------------------------------------------------------------------


def test_reset_cost_tracker_zeros_everything() -> None:
    add_usage(_make_usage(input_tokens=5000, output_tokens=3000))
    reset_cost_tracker()
    summary = get_cost_summary()
    assert summary["input_tokens"] == 0
    assert summary["output_tokens"] == 0
    assert summary["request_count"] == 0
    assert summary["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# format_total_cost
# ---------------------------------------------------------------------------


def test_format_total_cost_zero() -> None:
    reset_cost_tracker()
    assert format_total_cost() == ""


def test_format_total_cost_nonzero() -> None:
    reset_cost_tracker()
    add_usage(_make_usage(input_tokens=1000, output_tokens=500))
    result = format_total_cost()
    assert "Total cost:" in result
    assert "$" in result


# ---------------------------------------------------------------------------
# save_current_session_costs
# ---------------------------------------------------------------------------


def test_save_current_session_costs_is_noop() -> None:
    # Should not raise
    save_current_session_costs()
    save_current_session_costs(fps_metrics={"test": True})
