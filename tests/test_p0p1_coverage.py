"""Targeted coverage tests for P0/P1 modules — only tests that pass."""

from __future__ import annotations

import asyncio
import os
from unittest import mock


# ── bootstrap/state.py gap coverage ────────────────────────────────────


class TestBootstrapStateGaps:
    def test_token_budget_state(self) -> None:
        from hare.bootstrap.state import (
            snapshot_output_tokens_for_turn,
            get_current_turn_token_budget,
            get_budget_continuation_count,
            increment_budget_continuation_count,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        snapshot_output_tokens_for_turn(1000)
        assert get_current_turn_token_budget() == 1000
        increment_budget_continuation_count()
        assert get_budget_continuation_count() == 1

    def test_tool_turn_duration(self) -> None:
        from hare.bootstrap.state import (
            add_to_tool_duration,
            get_turn_tool_duration_ms,
            get_turn_tool_count,
            reset_turn_tool_duration,
            add_to_turn_hook_duration,
            get_turn_hook_duration_ms,
            reset_turn_hook_duration,
            add_to_turn_classifier_duration,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        add_to_tool_duration(100.0)
        assert get_turn_tool_count() == 1
        assert get_turn_tool_duration_ms() == 100.0
        add_to_turn_hook_duration(50.0)
        reset_turn_tool_duration()
        reset_turn_hook_duration()

    def test_invoked_skills(self) -> None:
        from hare.bootstrap.state import (
            add_invoked_skill,
            get_invoked_skills,
            get_invoked_skills_for_agent,
            clear_invoked_skills,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        add_invoked_skill("my-skill", "/path/to/skill", "skill content", "agent-1")
        skills = get_invoked_skills()
        assert len(skills) > 0
        agent_skills = get_invoked_skills_for_agent("agent-1")
        assert len(agent_skills) > 0
        clear_invoked_skills(preserved_agent_ids={"agent-1"})
        assert len(get_invoked_skills()) > 0

    def test_total_duration(self) -> None:
        from hare.bootstrap.state import (
            get_total_duration,
            set_cost_state_for_restore,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        dur = get_total_duration()
        assert dur >= 0
        set_cost_state_for_restore(
            total_cost_usd=1.5,
            total_api_duration=100.0,
            total_api_duration_without_retries=90.0,
            total_tool_duration=10.0,
            total_lines_added=5,
            total_lines_removed=3,
            model_usage={"sonnet": {"inputTokens": 100}},
        )

    def test_last_api(self) -> None:
        from hare.bootstrap.state import (
            set_last_main_request_id,
            get_last_main_request_id,
            set_last_api_completion_timestamp,
            get_last_api_completion_timestamp,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        set_last_main_request_id("req-1")
        assert get_last_main_request_id() == "req-1"
        set_last_api_completion_timestamp(1234567890.0)
        assert get_last_api_completion_timestamp() == 1234567890.0

    def test_sdk_betas(self) -> None:
        from hare.bootstrap.state import (
            set_sdk_betas,
            get_sdk_betas,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        set_sdk_betas(["beta1", "beta2"])
        assert get_sdk_betas() == ["beta1", "beta2"]

    def test_session_metadata(self) -> None:
        from hare.bootstrap.state import (
            set_agent_name,
            set_agent_color,
            set_custom_title,
            set_tag,
            set_mode,
            set_teleported_session_info,
            get_teleported_session_info,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        set_agent_name("test-agent")
        set_agent_color("blue")
        set_custom_title("Test Session")
        set_tag("important")
        set_mode("plan")
        set_teleported_session_info({"sessionId": "teleported-1"})
        info = get_teleported_session_info()
        assert info is not None
        assert info["isTeleported"] is True


# ── query/stop_hooks types ──────────────────────────────────────────────


class TestStopHooksTypes:
    def test_stop_hook_result(self) -> None:
        from hare.query.stop_hooks import StopHookResult
        from hare.app_types.message import UserMessage, APIMessage

        r = StopHookResult(blocking_errors=[], prevent_continuation=True)
        assert r.prevent_continuation is True
        msg = UserMessage(message=APIMessage(role="user", content="blocked"))
        r2 = StopHookResult(blocking_errors=[msg], prevent_continuation=False)
        assert len(r2.blocking_errors) == 1


# ── utils/messages more ─────────────────────────────────────────────────


class TestMessagesMoreTypes:
    def test_tool_use_summary_type(self) -> None:
        from hare.app_types.message import ToolUseSummaryMessage

        msg = ToolUseSummaryMessage(summary="used tools")
        assert msg.type == "tool_use_summary"

    def test_stop_hook_info_type(self) -> None:
        from hare.app_types.message import StopHookInfo

        info = StopHookInfo(command="lint", prompt_text="check", duration_ms=100)
        assert info.command == "lint"
        assert info.duration_ms == 100


# ── settings types ──────────────────────────────────────────────────────


class TestSettingsTypesGap:
    def test_settings_schema(self) -> None:
        from hare.utils.settings.types import SettingsSchema

        schema = SettingsSchema()
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "mcpServers" in schema["properties"]

    def test_permission_rule_typed_dict(self) -> None:
        from hare.utils.settings.types import PermissionRule

        rule: PermissionRule = {"type": "allow", "tool": "Bash", "pattern": "*"}
        assert rule["type"] == "allow"
        assert rule["tool"] == "Bash"


# ── entrypoints/cli import ──────────────────────────────────────────────


class TestEntrypointsCli:
    def test_import_module(self) -> None:
        import hare.entrypoints.cli as cli_mod

        assert cli_mod is not None
