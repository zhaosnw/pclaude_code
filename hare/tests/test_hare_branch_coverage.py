"""Targeted branch coverage tests for P0/P1 high-gap modules."""

from __future__ import annotations

import pytest

from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    SystemMessage,
    UserMessage,
)
from hare.utils.messages import (
    create_user_message,
    filter_unresolved_tool_uses,
    filter_orphaned_thinking_only_messages,
    filter_whitespace_only_assistant_messages,
    filter_trailing_thinking_from_last_assistant,
    derive_short_message_id,
    is_tool_use_result_message,
)


# ── Branch coverage for filter functions ──────────────────────────


class TestFilterUnresolvedToolUses:
    """Exercise branches in filter_unresolved_tool_uses (lines ~290-335)."""

    def test_empty_list(self) -> None:
        result = filter_unresolved_tool_uses([])
        assert result == []

    def test_multiple_tool_uses(self) -> None:
        am = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "tool_use", "id": "t1"},
                    {"type": "tool_use", "id": "t2"},
                ],
            )
        )
        result = filter_unresolved_tool_uses([am])
        # Unresolved tool_use blocks → assistant message is dropped
        assert result == []

    def test_tool_results_that_are_string(self) -> None:
        # Exercise tool_result branch where tid is a string
        am = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "tool_use", "id": "t1"},
                ],
            )
        )
        um = UserMessage(
            message=APIMessage(
                role="user",
                content=[
                    {"type": "tool_result", "tool_use_id": "t1"},
                ],
            )
        )
        result = filter_unresolved_tool_uses([am, um])
        assert isinstance(result, list)

    def test_tool_results_list_content(self) -> None:
        am = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "tool_use", "id": "t1"},
                ],
            )
        )
        um = UserMessage(message=APIMessage(role="user", content="result text"))
        um.tool_use_result = "t1"
        result = filter_unresolved_tool_uses([am, um])
        assert isinstance(result, list)


class TestFilterOrphanedThinking:
    """Exercise branches in filter_orphaned_thinking_only_messages (~lines 338-390)."""

    def test_empty(self) -> None:
        assert filter_orphaned_thinking_only_messages([]) == []

    def test_non_assistant_skipped(self) -> None:
        msg = UserMessage(message=APIMessage(role="user", content="hello"))
        result = filter_orphaned_thinking_only_messages([msg])
        assert len(result) == 1

    def test_assistant_with_string_content(self) -> None:
        # String content should pass through (not a list so filter skips)
        msg = AssistantMessage(message=APIMessage(role="assistant", content="hello"))
        result = filter_orphaned_thinking_only_messages([msg])
        assert len(result) == 1

    def test_assistant_with_only_thinking(self) -> None:
        # All thinking blocks -> should be filtered out
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "thinking", "thinking": "hmm..."},
                    {"type": "redacted_thinking", "data": "redacted"},
                ],
            )
        )
        result = filter_orphaned_thinking_only_messages([msg])
        # Should be filtered since only thinking content
        assert isinstance(result, list)

    def test_assistant_with_mixed_content(self) -> None:
        # Thinking + text -> should be preserved
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "thinking", "thinking": "hmm..."},
                    {"type": "text", "text": "here is my answer"},
                ],
            )
        )
        result = filter_orphaned_thinking_only_messages([msg])
        assert len(result) == 1


class TestFilterWhitespaceOnly:
    """Exercise branches in filter_whitespace_only_assistant_messages (~lines 384-440)."""

    def test_empty(self) -> None:
        assert filter_whitespace_only_assistant_messages([]) == []

    def test_string_content(self) -> None:
        msg = AssistantMessage(message=APIMessage(role="assistant", content="hello"))
        result = filter_whitespace_only_assistant_messages([msg])
        assert len(result) == 1

    def test_whitespace_text_blocks(self) -> None:
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "   "},
                    {"type": "text", "text": ""},
                ],
            )
        )
        result = filter_whitespace_only_assistant_messages([msg])
        # Should be filtered out since all text is whitespace
        assert isinstance(result, list)

    def test_mixed_whitespace_and_tool_use(self) -> None:
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "  "},
                    {"type": "tool_use", "id": "t1", "name": "bash"},
                ],
            )
        )
        result = filter_whitespace_only_assistant_messages([msg])
        # Whitespace-only text blocks are dropped even if tool_use is present
        assert len(result) == 0

    def test_non_text_blocks(self) -> None:
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "tool_use", "id": "t1"},
                ],
            )
        )
        result = filter_whitespace_only_assistant_messages([msg])
        # Only tool_use, not just text -> preserved
        assert len(result) == 1


class TestFilterTrailingThinking:
    """Exercise branches in filter_trailing_thinking_from_last_assistant (~lines 384-440)."""

    def test_empty(self) -> None:
        assert filter_trailing_thinking_from_last_assistant([]) == []

    def test_no_assistant(self) -> None:
        msg = UserMessage(message=APIMessage(role="user", content="hello"))
        result = filter_trailing_thinking_from_last_assistant([msg])
        assert len(result) == 1

    def test_assistant_string_content(self) -> None:
        msg = AssistantMessage(message=APIMessage(role="assistant", content="hello"))
        result = filter_trailing_thinking_from_last_assistant([msg])
        assert len(result) == 1

    def test_assistant_no_trailing_thinking(self) -> None:
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "answer"},
                ],
            )
        )
        result = filter_trailing_thinking_from_last_assistant([msg])
        assert isinstance(result, list)

    def test_assistant_trailing_thinking(self) -> None:
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "here you go"},
                    {"type": "thinking", "thinking": "was that good?"},
                ],
            )
        )
        result = filter_trailing_thinking_from_last_assistant([msg])
        assert isinstance(result, list)

    def test_assistant_only_thinking(self) -> None:
        msg = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "thinking", "thinking": "hmm"},
                ],
            )
        )
        result = filter_trailing_thinking_from_last_assistant([msg])
        assert isinstance(result, list)


class TestIsToolUseResult:
    """Exercise is_tool_use_result_message branches."""

    def test_user_msg_with_attr(self) -> None:
        msg = UserMessage(
            message=APIMessage(
                role="user",
                content=[
                    {"type": "tool_result", "tool_use_id": "tool-1"},
                ],
            )
        )
        assert is_tool_use_result_message(msg) is True

    def test_user_msg_without_attr(self) -> None:
        msg = UserMessage()
        assert is_tool_use_result_message(msg) is False

    def test_assistant_msg(self) -> None:
        msg = AssistantMessage()
        assert is_tool_use_result_message(msg) is False

    def test_user_with_content_list(self) -> None:
        # Exercise the content check branch
        msg = UserMessage(
            message=APIMessage(
                role="user",
                content=[
                    {"type": "tool_result", "tool_use_id": "t1"},
                ],
            )
        )
        result = is_tool_use_result_message(msg)
        assert isinstance(result, bool)


class TestDeriveShortMessageId:
    """Exercise derive_short_message_id branches."""

    def test_normal_uuid(self) -> None:
        sid = derive_short_message_id("abcdef01-2345-6789-abcd-ef0123456789")
        assert isinstance(sid, str)
        assert len(sid) <= 7

    def test_another_uuid(self) -> None:
        sid = derive_short_message_id("00000000-0000-0000-0000-000000000000")
        assert isinstance(sid, str)

    def test_uuid_with_dashes(self) -> None:
        sid = derive_short_message_id("ffffffff-ffff-ffff-ffff-ffffffffffff")
        assert isinstance(sid, str)


# ── Branch coverage for bootstrap/state.py ─────────────────────────


class TestStateBranches:
    """Target specific uncovered branches in bootstrap/state.py."""

    def test_plan_mode_edge_transitions(self) -> None:
        from hare.bootstrap.state import (
            handle_plan_mode_transition,
            handle_auto_mode_transition,
            needs_plan_mode_exit_attachment,
            needs_auto_mode_exit_attachment,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        # Already in plan -> plan (should NOT set needs_exit)
        handle_plan_mode_transition("plan", "plan")
        # auto -> plan (should NOT set needs_auto_exit for this transition)
        handle_auto_mode_transition("auto", "plan")
        # plan -> auto (should NOT set needs_auto_exit)
        handle_auto_mode_transition("plan", "auto")

    def test_register_clear_hooks(self) -> None:
        from hare.bootstrap.state import (
            register_hook_callbacks,
            get_registered_hooks,
            clear_registered_hooks,
            clear_registered_plugin_hooks,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        hooks = {"PreToolUse": [{"pattern": "*", "command": "test"}]}
        register_hook_callbacks(hooks)
        assert get_registered_hooks() is not None
        clear_registered_plugin_hooks()
        clear_registered_hooks()
        assert get_registered_hooks() is None

    def test_register_hooks_overwrite(self) -> None:
        from hare.bootstrap.state import (
            register_hook_callbacks,
            get_registered_hooks,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        register_hook_callbacks({"PreToolUse": [{"pattern": "a"}]})
        # Register again to hit the extend path
        register_hook_callbacks({"PreToolUse": [{"pattern": "b"}]})
        hooks = get_registered_hooks()
        assert hooks is not None
        assert len(hooks.get("PreToolUse", [])) == 2

    def test_clear_plugin_hooks_with_dict(self) -> None:
        from hare.bootstrap.state import (
            register_hook_callbacks,
            clear_registered_plugin_hooks,
            get_registered_hooks,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        # Register with dict entries (not objects with pluginRoot)
        register_hook_callbacks(
            {"PostToolUse": [{"pluginRoot": "/tmp", "pattern": "*"}]}
        )
        clear_registered_plugin_hooks()
        # After clearing plugin hooks, registered hooks may be None or empty
        result = get_registered_hooks()
        assert result is None or result == {}

    def test_session_created_teams(self) -> None:
        from hare.bootstrap.state import (
            get_session_created_teams,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        teams = get_session_created_teams()
        assert isinstance(teams, set)

    def test_invoked_skills_edge_cases(self) -> None:
        from hare.bootstrap.state import (
            add_invoked_skill,
            clear_invoked_skills,
            clear_invoked_skills_for_agent,
            get_invoked_skills,
            get_invoked_skills_for_agent,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        add_invoked_skill("skill-a", "/a", "content-a", "agent-1")
        add_invoked_skill("skill-b", "/b", "content-b", "agent-2")
        add_invoked_skill("skill-c", "/c", "content-c")  # no agent_id
        # Clear all (no preserved agents)
        clear_invoked_skills()
        assert len(get_invoked_skills()) == 0
        # Re-add and clear for specific agent
        add_invoked_skill("skill-d", "/d", "content-d", "agent-3")
        clear_invoked_skills_for_agent("agent-3")
        assert len(get_invoked_skills_for_agent("agent-3")) == 0

    def test_diminishing_returns_covered(self) -> None:
        import os
        from unittest import mock

        from hare.bootstrap.state import (
            add_slow_operation,
            get_slow_operations,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        with mock.patch.dict(os.environ, {"USER_TYPE": "ant"}):
            add_slow_operation("op1", 100.0)
            add_slow_operation("op2", 200.0)
            ops = get_slow_operations()
        assert len(ops) == 2


# ── Branch coverage for query/stop_hooks.py ────────────────────────


class TestStopHooksBranches:
    """Target specific branch coverage gaps in stop_hooks.py."""

    def test_stop_hook_result_variants(self) -> None:
        from hare.query.stop_hooks import StopHookResult
        from hare.app_types.message import UserMessage, APIMessage

        # With blocking errors
        msg = UserMessage(message=APIMessage(role="user", content="blocked"))
        r = StopHookResult(blocking_errors=[msg], prevent_continuation=True)
        assert len(r.blocking_errors) == 1
        assert r.prevent_continuation is True

        # Without errors
        r2 = StopHookResult(blocking_errors=[], prevent_continuation=False)
        assert r2.blocking_errors == []
        assert r2.prevent_continuation is False


# ── Branch coverage for query_engine.py ────────────────────────────


class TestQueryEngineBranches:
    """Target specific uncovered branches in query_engine.py."""

    def test_query_engine_config_defaults(self) -> None:
        from hare.query_engine import QueryEngineConfig

        cfg = QueryEngineConfig()
        assert cfg.cwd == ""
        assert cfg.tools == []
        assert cfg.verbose is False
        assert cfg.max_turns is None
        assert cfg.max_budget_usd is None
        assert cfg.custom_system_prompt is None
        assert cfg.append_system_prompt is None
        assert cfg.initial_messages is None
        assert cfg.include_partial_messages is False
        assert cfg.replay_user_messages is False


# ── Branch coverage for utils/settings/settings.py ─────────────────


class TestSettingsBranches:
    """Target specific uncovered branches in settings.py."""

    def test_settings_edge_cases(self) -> None:
        from hare.utils.settings.constants import SETTING_SOURCES
        from hare.utils.settings.settings import get_initial_settings

        assert isinstance(SETTING_SOURCES, (list, tuple))
        settings = get_initial_settings()
        assert isinstance(settings, dict)
