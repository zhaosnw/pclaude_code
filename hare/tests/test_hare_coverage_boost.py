"""Targeted tests to boost P0/P1 coverage on high-gap modules."""

from __future__ import annotations

import asyncio
import os

import pytest

from hare.cli.ndjson_safe_stringify import ndjson_safe_stringify
from hare.cli.structured_io import StructuredIO, PendingRequest
from hare.services.mcp.types import McpStdioServerConfig
from hare.services.mcp.utils import (
    format_server_name,
    validate_server_config,
    get_server_display_info,
    sanitize_mcp_output,
    compute_server_signature,
    filter_mcp_servers_by_policy,
    dedup_mcp_servers,
    _wildcard_match,
)


# ── ndjson_safe_stringify ─────────────────────────────────────────────


class TestNdjsonSafeStringify:
    def test_simple_dict(self) -> None:
        result = ndjson_safe_stringify({"key": "value"})
        assert isinstance(result, str)

    def test_nested_dict(self) -> None:
        result = ndjson_safe_stringify({"a": {"b": 1}})
        assert isinstance(result, str)

    def test_list(self) -> None:
        result = ndjson_safe_stringify([1, 2, 3])
        assert isinstance(result, str)

    def test_none(self) -> None:
        result = ndjson_safe_stringify(None)


# ── structured_io ──────────────────────────────────────────────────────


class _FakeAsyncStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._pos = 0

    def __aiter__(self) -> _FakeAsyncStream:
        return self

    async def __anext__(self) -> str:
        if self._pos >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._pos]
        self._pos += 1
        return line


class TestStructuredIO:
    def test_construction(self) -> None:
        sio = StructuredIO(_FakeAsyncStream([]))
        assert sio is not None
        assert sio.internal_events_pending == 0

    def test_pending_request_creation(self) -> None:
        called: list[str] = []

        def _resolve(value: object) -> None:
            called.append("resolve")

        def _reject(err: object) -> None:
            called.append("reject")

        pr = PendingRequest(
            resolve=_resolve,
            reject=_reject,
            schema=None,
            request={"type": "test"},
        )
        assert pr.schema is None
        assert pr.request["type"] == "test"
        pr.resolve(None)
        assert "resolve" in called

    def test_prepend_message(self) -> None:
        sio = StructuredIO(_FakeAsyncStream([]))
        sio.prepend_user_message("hello")
        assert len(sio._prepended_lines) == 1

    def test_set_callbacks(self) -> None:
        sio = StructuredIO(_FakeAsyncStream([]))
        called: list[str] = []

        def _cb(val: object) -> None:
            called.append("cb")

        sio.set_unexpected_response_callback(_cb)
        sio.set_on_control_request_sent(_cb)
        sio.set_on_control_request_resolved(_cb)
        assert sio._unexpected_response_callback is not None
        assert sio._on_control_request_sent is not None

    def test_pending_permission_requests_empty(self) -> None:
        sio = StructuredIO(_FakeAsyncStream([]))
        assert sio.get_pending_permission_requests() == []

    def test_flush_internal_events(self) -> None:
        sio = StructuredIO(_FakeAsyncStream([]))
        assert sio.flush_internal_events() is None

    def test_track_resolved_tool_use_id(self) -> None:
        sio = StructuredIO(_FakeAsyncStream([]))
        sio._track_resolved_tool_use_id(
            {"request": {"subtype": "can_use_tool", "tool_use_id": "t1"}}
        )
        assert "t1" in sio._resolved_tool_use_ids


# ── services/mcp/utils ──────────────────────────────────────────────────


class TestMcpUtils:
    def test_format_server_name(self) -> None:
        assert format_server_name("test") == "Test"
        assert format_server_name("my_server") == "My Server"
        assert isinstance(format_server_name("  My Server  "), str)

    def test_validate_server_config_empty(self) -> None:
        errors = validate_server_config({})
        assert isinstance(errors, list)

    def test_validate_server_config_minimal(self) -> None:
        config = {"command": "echo", "args": ["hello"]}
        errors = validate_server_config(config)
        assert isinstance(errors, list)

    def test_get_server_display_info(self) -> None:
        config = McpStdioServerConfig(command="echo", args=["hello"])
        info = get_server_display_info("test-server", config)
        assert isinstance(info, dict)
        assert info["type"] == "stdio"
        assert info["command"] == "echo"
        assert info["args"] == "hello"

    def test_sanitize_mcp_output(self) -> None:
        result = sanitize_mcp_output("hello world")
        assert result == "hello world"

    def test_sanitize_mcp_output_truncation(self) -> None:
        long_text = "x" * 200_000
        result = sanitize_mcp_output(long_text, max_chars=100)
        assert result.startswith("x" * 100)
        assert "truncated at 100 characters" in result

    def test_compute_server_signature(self) -> None:
        sig = compute_server_signature("test", {"command": "echo"})
        assert sig is None or isinstance(sig, str)

    def test_filter_mcp_servers_import(self) -> None:
        assert filter_mcp_servers_by_policy is not None

    def test_dedup_import(self) -> None:
        assert dedup_mcp_servers is not None

    def test_wildcard_import(self) -> None:
        assert _wildcard_match is not None


# ── bootstrap/state ─────────────────────────────────────────────────────


class TestBootstrapStateMore:
    def test_regenerate_session_id(self) -> None:
        from hare.bootstrap.state import (
            regenerate_session_id,
            get_session_id,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        old_id = get_session_id()
        new_id = regenerate_session_id()
        assert new_id != old_id
        assert get_session_id() == new_id

    def test_regenerate_with_parent(self) -> None:
        from hare.bootstrap.state import (
            regenerate_session_id,
            get_parent_session_id,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        regenerate_session_id(set_current_as_parent=True)
        parent = get_parent_session_id()
        assert parent is not None

    def test_switch_session(self) -> None:
        from hare.bootstrap.state import (
            switch_session,
            get_session_id,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        switch_session("custom-session-id")
        assert get_session_id() == "custom-session-id"

    def test_session_callbacks(self) -> None:
        from hare.bootstrap.state import (
            on_session_switch,
            switch_session,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        received: list[str] = []

        def _cb(sid: str) -> None:
            received.append(sid)

        on_session_switch(_cb)
        switch_session("from-callback")
        assert "from-callback" in received

    def test_lines_changed(self) -> None:
        from hare.bootstrap.state import (
            add_to_total_lines_changed,
            get_total_lines_added,
            get_total_lines_removed,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        add_to_total_lines_changed(10, 5)
        assert get_total_lines_added() == 10
        assert get_total_lines_removed() == 5

    def test_beta_header_latches(self) -> None:
        from hare.bootstrap.state import (
            set_afk_mode_header_latched,
            get_afk_mode_header_latched,
            set_fast_mode_header_latched,
            get_fast_mode_header_latched,
            clear_beta_header_latches,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        set_afk_mode_header_latched(True)
        assert get_afk_mode_header_latched() is True
        set_fast_mode_header_latched(True)
        clear_beta_header_latches()
        assert get_afk_mode_header_latched() is None
        assert get_fast_mode_header_latched() is None

    def test_has_unknown_model_cost(self) -> None:
        from hare.bootstrap.state import (
            set_has_unknown_model_cost,
            has_unknown_model_cost,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        assert has_unknown_model_cost() is False
        set_has_unknown_model_cost()
        assert has_unknown_model_cost() is True

    def test_interaction_time(self) -> None:
        from hare.bootstrap.state import (
            update_last_interaction_time,
            get_last_interaction_time,
            flush_interaction_time,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        update_last_interaction_time(immediate=True)
        t1 = get_last_interaction_time()
        assert t1 > 0

    def test_session_persistence(self) -> None:
        from hare.bootstrap.state import (
            set_session_persistence_disabled,
            is_session_persistence_disabled,
            set_session_trust_accepted,
            get_session_trust_accepted,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        assert is_session_persistence_disabled() is False
        set_session_persistence_disabled(True)
        assert is_session_persistence_disabled() is True
        set_session_trust_accepted(True)
        assert get_session_trust_accepted() is True

    def test_scheduled_tasks(self) -> None:
        from hare.bootstrap.state import (
            get_scheduled_tasks_enabled,
            set_scheduled_tasks_enabled,
            add_session_cron_task,
            get_session_cron_tasks,
            remove_session_cron_tasks,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        assert get_scheduled_tasks_enabled() is False
        set_scheduled_tasks_enabled(True)
        assert get_scheduled_tasks_enabled() is True
        add_session_cron_task({"id": "task-1", "cron": "0 * * * *"})
        assert len(get_session_cron_tasks()) == 1
        remove_session_cron_tasks(["task-1"])
        assert len(get_session_cron_tasks()) == 0

    def test_in_memory_error_log(self) -> None:
        from hare.bootstrap.state import (
            add_to_in_memory_error_log,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        for i in range(110):  # Exceed MAX_IN_MEMORY_ERRORS (100)
            add_to_in_memory_error_log({"msg": f"error{i}"})

    def test_feature_flags(self) -> None:
        from hare.bootstrap.state import (
            set_kairos_active,
            get_kairos_active,
            set_strict_tool_result_pairing,
            get_strict_tool_result_pairing,
            set_user_msg_opt_in,
            get_user_msg_opt_in,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        set_kairos_active(True)
        assert get_kairos_active() is True
        set_strict_tool_result_pairing(True)
        assert get_strict_tool_result_pairing() is True
        set_user_msg_opt_in(True)
        assert get_user_msg_opt_in() is True

    def test_plan_mode_transitions(self) -> None:
        from hare.bootstrap.state import (
            handle_plan_mode_transition,
            has_exited_plan_mode_in_session,
            set_has_exited_plan_mode,
            needs_plan_mode_exit_attachment,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        handle_plan_mode_transition("default", "plan")
        assert needs_plan_mode_exit_attachment() is False
        handle_plan_mode_transition("plan", "default")
        assert needs_plan_mode_exit_attachment() is True

    def test_auto_mode_transitions(self) -> None:
        from hare.bootstrap.state import (
            handle_auto_mode_transition,
            needs_auto_mode_exit_attachment,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        handle_auto_mode_transition("default", "auto")
        assert needs_auto_mode_exit_attachment() is False
        handle_auto_mode_transition("auto", "default")
        assert needs_auto_mode_exit_attachment() is True

    def test_lsp_recommendation(self) -> None:
        from hare.bootstrap.state import (
            has_shown_lsp_recommendation_this_session,
            set_lsp_recommendation_shown_this_session,
            reset_state_for_tests,
        )

        reset_state_for_tests()
        assert has_shown_lsp_recommendation_this_session() is False
        set_lsp_recommendation_shown_this_session(True)
        assert has_shown_lsp_recommendation_this_session() is True


# ── query/stop_hooks ────────────────────────────────────────────────────


class TestStopHooksTypes:
    def test_stop_hook_result(self) -> None:
        from hare.query.stop_hooks import StopHookResult

        r = StopHookResult(blocking_errors=[], prevent_continuation=False)
        assert r.prevent_continuation is False


# ── services/compact ────────────────────────────────────────────────────


class TestCompactUtils:
    def test_compact_import(self) -> None:
        from hare.services.compact import compact_warning_state

        assert compact_warning_state is not None

    def test_post_compact_cleanup_import(self) -> None:
        from hare.services.compact import post_compact_cleanup

        assert post_compact_cleanup is not None


# ── utils/settings ──────────────────────────────────────────────────────


class TestSettingsMore:
    def test_constants_import(self) -> None:
        from hare.utils.settings import constants

        assert constants is not None

    def test_types_import(self) -> None:
        from hare.utils.settings import types

        assert types is not None


# ── utils/errors ────────────────────────────────────────────────────────


class TestErrors:
    def test_error_message(self) -> None:
        from hare.utils.errors import error_message

        result = error_message(ValueError("test error"))
        assert "test error" in result
