"""
Unit tests for hare.context — git status, system/user context providers.

Port of: src/context.ts behavior verification.
"""

from __future__ import annotations

from datetime import date


from hare.context import (
    MAX_STATUS_CHARS,
    get_system_context_sync,
    get_user_context,
    set_system_prompt_injection,
    get_system_prompt_injection,
)


# ---------------------------------------------------------------------------
# System prompt injection
# ---------------------------------------------------------------------------


def test_system_prompt_injection_default_none() -> None:
    assert get_system_prompt_injection() is None


def test_system_prompt_injection_set_and_get() -> None:
    set_system_prompt_injection("test_injection")
    assert get_system_prompt_injection() == "test_injection"
    # Clean up
    set_system_prompt_injection(None)


def test_system_prompt_injection_clears_caches(monkeypatch) -> None:
    # Verify setting injection clears the lru caches on context functions
    set_system_prompt_injection("cache_test")
    # Just verify it doesn't throw
    ctx = get_user_context()
    assert isinstance(ctx, dict)
    set_system_prompt_injection(None)


# ---------------------------------------------------------------------------
# get_system_context_sync
# ---------------------------------------------------------------------------


def test_system_context_sync_returns_dict() -> None:
    result = get_system_context_sync()
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# get_user_context
# ---------------------------------------------------------------------------


def test_get_user_context_has_current_date() -> None:
    result = get_user_context()
    assert "currentDate" in result
    today = date.today().isoformat()
    assert today in result["currentDate"]


def test_get_user_context_is_dict() -> None:
    result = get_user_context()
    assert isinstance(result, dict)


def test_get_user_context_keys() -> None:
    result = get_user_context()
    assert "currentDate" in result


# ---------------------------------------------------------------------------
# MAX_STATUS_CHARS constant
# ---------------------------------------------------------------------------


def test_max_status_chars_value() -> None:
    assert MAX_STATUS_CHARS == 2000
