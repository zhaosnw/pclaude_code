"""
Unit tests for hare.bootstrap.state — global session state.

Port of: src/bootstrap/state.ts behavior verification.

Tests cover session lifecycle, cost tracking, turn counters, and hooks.
"""

from __future__ import annotations

import time
import uuid

import pytest

from hare.bootstrap.state import (
    add_invoked_skill,
    add_to_total_cost_state,
    add_to_total_duration_state,
    clear_invoked_skills,
    get_cwd,
    get_invoked_skills,
    get_is_interactive,
    get_model_usage,
    get_parent_session_id,
    get_session_id,
    get_total_api_duration,
    get_total_cost_usd,
    has_exited_plan_mode_in_session,
    handle_plan_mode_transition,
    is_session_persistence_disabled,
    regenerate_session_id,
    reset_cost_state,
    reset_state_for_tests,
    set_cwd,
    set_is_interactive,
    set_session_bypass_permissions_mode,
    set_session_persistence_disabled,
    set_session_trust_accepted,
    switch_session,
    update_last_interaction_time,
)


# ---------------------------------------------------------------------------
# Fixture: reset state before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Reset bootstrap state so tests are isolated."""
    reset_state_for_tests()
    yield
    reset_state_for_tests()


# ---------------------------------------------------------------------------
# Session ID
# ---------------------------------------------------------------------------


def test_get_session_id_returns_string() -> None:
    sid = get_session_id()
    assert isinstance(sid, str)
    assert len(sid) > 0


def test_get_session_id_is_valid_uuid() -> None:
    sid = get_session_id()
    uuid.UUID(sid)  # should not raise


def test_regenerate_session_id_changes_id() -> None:
    old = get_session_id()
    new = regenerate_session_id()
    assert new != old


def test_regenerate_session_id_with_parent() -> None:
    old = get_session_id()
    new = regenerate_session_id(set_current_as_parent=True)
    assert get_parent_session_id() == old
    assert new != old


def test_get_parent_session_id_default_none() -> None:
    # After reset, parent should be None
    assert get_parent_session_id() is None


def test_switch_session_updates_id() -> None:
    new_id = str(uuid.uuid4())
    switch_session(new_id)
    assert get_session_id() == new_id


# ---------------------------------------------------------------------------
# CWD
# ---------------------------------------------------------------------------


def test_get_cwd_returns_string() -> None:
    assert isinstance(get_cwd(), str)


def test_set_cwd_updates() -> None:
    set_cwd("/tmp/test/path")
    assert get_cwd() == "/tmp/test/path"


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


def test_add_to_total_cost_updates() -> None:
    reset_cost_state()
    add_to_total_cost_state(1.5, {"model": "sonnet"}, "sonnet")
    assert get_total_cost_usd() == 1.5


def test_add_to_total_duration_updates() -> None:
    reset_cost_state()
    add_to_total_duration_state(2.0, 1.8)
    assert get_total_api_duration() == 2.0


def test_reset_cost_state_zeroes_all() -> None:
    add_to_total_cost_state(5.0, {}, "sonnet")
    reset_cost_state()
    assert get_total_cost_usd() == 0.0
    assert get_total_api_duration() == 0.0


# ---------------------------------------------------------------------------
# Interactive
# ---------------------------------------------------------------------------


def test_get_is_interactive_default() -> None:
    # Default after reset is False
    assert get_is_interactive() is False


def test_set_is_interactive() -> None:
    set_is_interactive(True)
    assert get_is_interactive() is True
    set_is_interactive(False)
    assert get_is_interactive() is False


# ---------------------------------------------------------------------------
# Model usage
# ---------------------------------------------------------------------------


def test_get_model_usage_starts_empty() -> None:
    assert get_model_usage() == {}


def test_model_usage_after_add_cost() -> None:
    usage_data = {"inputTokens": 100, "outputTokens": 50}
    add_to_total_cost_state(0.01, usage_data, "sonnet")
    usage = get_model_usage()
    assert "sonnet" in usage
    assert usage["sonnet"] == usage_data


# ---------------------------------------------------------------------------
# Persistence / trust
# ---------------------------------------------------------------------------


def test_session_persistence_default() -> None:
    assert is_session_persistence_disabled() is False


def test_set_session_persistence_disabled() -> None:
    set_session_persistence_disabled(True)
    assert is_session_persistence_disabled() is True


def test_session_trust_default() -> None:
    from hare.bootstrap.state import get_session_trust_accepted

    assert get_session_trust_accepted() is False


def test_set_session_trust_accepted() -> None:
    set_session_trust_accepted(True)
    from hare.bootstrap.state import get_session_trust_accepted

    assert get_session_trust_accepted() is True


# ---------------------------------------------------------------------------
# Bypass permissions
# ---------------------------------------------------------------------------


def test_bypass_permissions_default() -> None:
    from hare.bootstrap.state import get_session_bypass_permissions_mode

    assert get_session_bypass_permissions_mode() is False


def test_set_session_bypass_permissions_mode() -> None:
    set_session_bypass_permissions_mode(True)
    from hare.bootstrap.state import get_session_bypass_permissions_mode

    assert get_session_bypass_permissions_mode() is True


# ---------------------------------------------------------------------------
# Plan mode
# ---------------------------------------------------------------------------


def test_has_exited_plan_mode_default() -> None:
    assert has_exited_plan_mode_in_session() is False


def test_handle_plan_mode_transition_enter() -> None:
    handle_plan_mode_transition("default", "plan")
    from hare.bootstrap.state import needs_plan_mode_exit_attachment

    assert needs_plan_mode_exit_attachment() is False


def test_handle_plan_mode_transition_exit() -> None:
    handle_plan_mode_transition("plan", "default")
    from hare.bootstrap.state import needs_plan_mode_exit_attachment

    assert needs_plan_mode_exit_attachment() is True


# ---------------------------------------------------------------------------
# Interaction time
# ---------------------------------------------------------------------------


def test_update_last_interaction_time_immediate() -> None:
    before = time.time() * 1000
    update_last_interaction_time(immediate=True)
    from hare.bootstrap.state import get_last_interaction_time

    after = get_last_interaction_time()
    assert after >= before


# ---------------------------------------------------------------------------
# Invoked skills
# ---------------------------------------------------------------------------


def test_add_and_get_invoked_skills() -> None:
    add_invoked_skill("test-skill", "/path/to/skill", "skill content")
    skills = get_invoked_skills()
    # Key format: ":test-skill" (no agent_id → empty string prefix)
    keys = list(skills.keys())
    assert any("test-skill" in k for k in keys)


def test_clear_invoked_skills() -> None:
    add_invoked_skill("skill1", "/path/1", "content1")
    clear_invoked_skills()
    assert get_invoked_skills() == {}


def test_clear_invoked_skills_preserves_agents() -> None:
    add_invoked_skill("skill1", "/path/1", "content1", agent_id="agent_a")
    clear_invoked_skills(preserved_agent_ids={"agent_a"})
    skills = get_invoked_skills()
    assert len(skills) == 1
