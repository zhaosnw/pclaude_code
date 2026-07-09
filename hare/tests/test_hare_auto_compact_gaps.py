"""Coverage gap tests for auto_compact.py."""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock


class TestAutoCompactGaps:
    def test_get_effective_window_default(self) -> None:
        from hare.services.compact.auto_compact import get_effective_context_window_size

        with patch.dict(os.environ, {}, clear=True):
            w = get_effective_context_window_size("claude-sonnet-4-20250514")
            assert w > 0

    def test_get_effective_window_with_env_valid(self) -> None:
        from hare.services.compact.auto_compact import get_effective_context_window_size

        with patch.dict(
            os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"}, clear=True
        ):
            w = get_effective_context_window_size("claude-sonnet-4-20250514")
            assert w <= 100000

    def test_get_effective_window_with_env_invalid(self) -> None:
        from hare.services.compact.auto_compact import get_effective_context_window_size

        with patch.dict(
            os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "not_a_number"}, clear=True
        ):
            w = get_effective_context_window_size("claude-sonnet-4-20250514")
            assert w > 0  # Should fall back to default

    def test_get_effective_window_with_env_zero(self) -> None:
        from hare.services.compact.auto_compact import get_effective_context_window_size

        with patch.dict(
            os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "0"}, clear=True
        ):
            w = get_effective_context_window_size("claude-sonnet-4-20250514")
            assert w > 0  # 0 is not > 0, so uses default

    def test_get_auto_compact_threshold(self) -> None:
        from hare.services.compact.auto_compact import get_auto_compact_threshold

        t = get_auto_compact_threshold("claude-sonnet-4-20250514")
        assert t >= 0

    def test_token_count_with_estimation_empty(self) -> None:
        from hare.services.compact.auto_compact import _token_count_with_estimation

        c = _token_count_with_estimation([])
        assert c >= 0

    def test_token_count_with_estimation_dict_msgs(self) -> None:
        from hare.services.compact.auto_compact import _token_count_with_estimation

        msgs = [{"message": {"content": "hello world"}}]
        c = _token_count_with_estimation(msgs)
        assert c > 0

    def test_token_count_with_estimation_obj_msgs(self) -> None:
        from hare.services.compact.auto_compact import _token_count_with_estimation
        from hare.app_types.message import UserMessage, APIMessage

        msg = UserMessage(
            message=APIMessage(role="user", content=[{"type": "text", "text": "hello"}])
        )
        c = _token_count_with_estimation([msg])
        assert c >= 0

    def test_is_auto_compact_enabled_default(self) -> None:
        from hare.services.compact.auto_compact import is_auto_compact_enabled

        with patch.dict(os.environ, {}, clear=True):
            assert is_auto_compact_enabled() is True

    def test_is_auto_compact_disabled(self) -> None:
        from hare.services.compact.auto_compact import is_auto_compact_enabled

        with patch.dict(os.environ, {"DISABLE_COMPACT": "1"}, clear=True):
            assert is_auto_compact_enabled() is False

    def test_is_auto_compact_disabled_alt(self) -> None:
        from hare.services.compact.auto_compact import is_auto_compact_enabled

        with patch.dict(os.environ, {"DISABLE_AUTO_COMPACT": "1"}, clear=True):
            assert is_auto_compact_enabled() is False

    def test_calculate_token_warning_state_ok(self) -> None:
        from hare.services.compact.auto_compact import calculate_token_warning_state

        s = calculate_token_warning_state(0, "claude-sonnet-4-20250514")
        assert "percentLeft" in s
        assert "isAboveWarningThreshold" in s
        assert not s["isAboveWarningThreshold"]

    def test_calculate_token_warning_state_high(self) -> None:
        from hare.services.compact.auto_compact import calculate_token_warning_state

        s = calculate_token_warning_state(180000, "claude-sonnet-4-20250514")
        assert "percentLeft" in s
        assert "isAboveAutoCompactThreshold" in s
        assert s["isAboveAutoCompactThreshold"]
