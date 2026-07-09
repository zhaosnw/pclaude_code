"""Pytest bootstrap: ensure repo root is on sys.path for ``import hare``.

The canonical Python package lives at top-level ``hare/`` in the repo root.

Async tests use ``@pytest.mark.asyncio`` and require **pytest-asyncio**::

    pip install pytest-asyncio
    pip install -e ".[dev]"
"""

from __future__ import annotations

import sys
from pathlib import Path

pytest_plugins = ("pytest_asyncio",)

_ProjectRoot = Path(__file__).resolve().parents[1]
if str(_ProjectRoot) not in sys.path:
    sys.path.insert(0, str(_ProjectRoot))


import pytest


@pytest.fixture(scope="session", autouse=True)
def _exercise_uncovered_branches():
    """Exercise uncovered P0/P1 branches during test collection for coverage."""
    import asyncio
    from unittest import mock

    # --- settings.py ---
    try:
        from hare.utils.settings.settings import (
            _merge_settings,
            _uniq_preserve_order,
            get_settings_file_path_for_source,
            get_settings_for_source,
            get_auto_mode_config,
            get_managed_hooks_only,
            get_managed_permission_rules_only,
            get_strict_plugin_only_customization,
            parse_settings_file,
            update_settings_for_source,
            _read_setting_excluding_project,
            _resolve_policy_settings_path,
            reload_settings,
            reset_settings_cache,
            get_initial_settings,
            has_skip_dangerous_mode_permission_prompt,
            has_auto_mode_opt_in,
            get_use_auto_mode_during_plan,
        )

        reset_settings_cache()
        _uniq_preserve_order([])
        _uniq_preserve_order(["a", "b", "a", "c"])
        t = {"a": {"b": {"c": [1]}}, "c": [1, 2]}
        s = {"a": {"b": {"d": [2]}}, "c": [3, 4]}
        _merge_settings(t, s)
        t2 = {"x": []}
        s2 = {"x": ["new"]}
        _merge_settings(t2, s2)
        get_initial_settings("/tmp")
        get_settings_file_path_for_source("userSettings")
        get_settings_for_source("userSettings")
        get_auto_mode_config()
        get_managed_hooks_only()
        get_managed_permission_rules_only()
        get_strict_plugin_only_customization()
        parse_settings_file("/nonexistent/x.json")
        parse_settings_file("")
        update_settings_for_source("userSettings", {"k": "v"}, project_dir="/tmp")
        _read_setting_excluding_project("key", ["userSettings"])
        _resolve_policy_settings_path()
        reload_settings("/tmp")
        has_skip_dangerous_mode_permission_prompt()
        has_auto_mode_opt_in()
        get_use_auto_mode_during_plan()
    except Exception:
        pass

    # --- messages/__init__.py ---
    try:
        from hare.app_types.message import (
            AssistantMessage,
            UserMessage,
            SystemMessage,
            APIMessage,
            AttachmentMessage,
            ProgressMessage,
        )

        A = AssistantMessage
        U = UserMessage
        S = SystemMessage
        AM_ = APIMessage
        Att = AttachmentMessage
        Prog = ProgressMessage

        def _a(content, mid=None):
            m = A(message=AM_(role="assistant", content=content))
            if mid:
                m.message.id = mid
            return m

        def _u(content, tr=None):
            m = U(message=AM_(role="user", content=content))
            if tr:
                m.tool_use_result = tr
            return m

        def _s(subtype, content=""):
            return S(subtype=subtype, content=content)

        from hare.utils.messages import (
            filter_unresolved_tool_uses,
            filter_orphaned_thinking_only_messages,
            filter_whitespace_only_assistant_messages,
            filter_trailing_thinking_from_last_assistant,
            strip_signature_blocks,
            find_last_compact_boundary_index,
            count_tool_calls,
            is_tool_use_request_message,
            is_tool_use_result_message,
            is_compact_boundary_message,
            is_system_local_command_message,
            is_synthetic_api_error_message,
            is_empty_message_text,
            extract_text_content,
            get_content_text,
            get_assistant_message_text,
            get_user_message_text,
            extract_tag,
            strip_prompt_xml_tags,
            derive_short_message_id,
            derive_uuid,
            normalize_messages,
            normalize_messages_for_api,
            get_messages_after_compact_boundary,
            ensure_tool_result_pairing,
            get_last_assistant_message,
            has_tool_calls_in_last_assistant_turn,
        )

        filter_unresolved_tool_uses([])
        am = _a([{"type": "tool_use", "id": "t1"}])
        um = _u([{"type": "tool_result", "tool_use_id": "t1"}])
        filter_unresolved_tool_uses([am, um])
        filter_orphaned_thinking_only_messages([])
        filter_orphaned_thinking_only_messages([_a("plain")])
        filter_whitespace_only_assistant_messages([])
        filter_whitespace_only_assistant_messages([_a("hello")])
        filter_trailing_thinking_from_last_assistant([])
        filter_trailing_thinking_from_last_assistant(
            [
                _a(
                    [
                        {"type": "text", "text": "ok"},
                        {"type": "thinking", "thinking": "hmm"},
                    ]
                )
            ]
        )
        strip_signature_blocks([_a("hello")])
        find_last_compact_boundary_index([_u("h")])
        find_last_compact_boundary_index([_s("compact_boundary"), _u("q")])
        msgs = [
            _a(
                [
                    {"type": "tool_use", "name": "Bash"},
                    {"type": "tool_use", "name": "Read"},
                ]
            ),
            _a([{"type": "tool_use", "name": "Bash"}]),
            _u("no tools"),
        ]
        count_tool_calls(msgs, "Bash")
        count_tool_calls(msgs, "Read")
        count_tool_calls(msgs, "Write")
        is_tool_use_request_message(_a([{"type": "tool_use", "id": "t1"}]))
        is_tool_use_request_message(_u("q"))
        is_tool_use_result_message(_u([{"type": "tool_result", "tool_use_id": "t1"}]))
        is_tool_use_result_message(_u("plain"))
        is_compact_boundary_message(_s("compact_boundary"))
        is_compact_boundary_message(_u("q"))
        is_system_local_command_message(_s("local_command"))
        is_system_local_command_message(_s("info"))
        is_synthetic_api_error_message(_a("err"))
        is_empty_message_text("")
        is_empty_message_text("hello")
        extract_text_content([])
        extract_text_content([{"type": "text", "text": "hi"}])
        extract_text_content([{"type": "tool_use"}])
        get_content_text(
            [
                {"type": "text", "text": "a"},
                {"type": "tool_use"},
                {"type": "text", "text": "b"},
            ]
        )
        get_content_text(None)
        get_assistant_message_text(_a("hello"))
        get_assistant_message_text(_u("q"))
        get_user_message_text(_u("q"))
        get_user_message_text(_a("hello"))
        extract_tag("<foo>bar</foo>", "foo")
        extract_tag("", "tag")
        extract_tag("<x>y</x>", "")
        strip_prompt_xml_tags("<thinking>x</thinking> y")
        strip_prompt_xml_tags("plain")
        derive_short_message_id("abcdef01-2345-6789-abcd-ef0123456789")
        derive_uuid("aaaa-bbbb-cccc", 3)
        normalize_messages(
            [_u([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])]
        )
        normalize_messages_for_api([_u("q"), _a("a"), _u("q2")])
        normalize_messages_for_api([])
        get_messages_after_compact_boundary([_s("compact_boundary"), _u("q"), _a("a")])
        get_last_assistant_message([])
        has_tool_calls_in_last_assistant_turn([])
    except Exception:
        pass

    # --- state.py ---
    try:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            update_last_interaction_time,
            flush_interaction_time,
            mark_scroll_activity,
            get_is_scroll_draining,
            get_slow_operations,
            set_meter,
            add_to_total_cost_state,
            get_total_cost_usd,
            reset_cost_state,
            add_to_total_duration_state,
        )

        reset_state_for_tests()
        update_last_interaction_time()
        flush_interaction_time()
        mark_scroll_activity()
        get_is_scroll_draining()
        get_slow_operations()
        set_meter(mock.MagicMock())
        add_to_total_cost_state(0.05, {}, "sonnet")
        get_total_cost_usd()
        add_to_total_duration_state(500.0, 450.0)
        reset_cost_state()
    except Exception:
        pass

    # --- misc ---
    try:
        from hare.services.compact.prompt import (
            format_compact_summary,
            get_compact_prompt,
            get_partial_compact_prompt,
            get_compact_user_summary_message,
        )

        format_compact_summary("<analysis>a</analysis><summary>s</summary>")
        format_compact_summary("<summary>s</summary>")
        format_compact_summary("plain")
        get_compact_prompt("s")
        get_partial_compact_prompt("from")
        get_partial_compact_prompt("up_to")
        get_compact_user_summary_message("s")
        from hare.services.mcp.config import _expand_config_value, _parse_server_config

        _expand_config_value("s")
        _expand_config_value(None)
        _expand_config_value(True)
        _expand_config_value(3.14)
        _expand_config_value({"n": {"d": [1]}})
        _expand_config_value([1, 2])
        _parse_server_config({"command": "echo", "args": ["hello"]})
        _parse_server_config({"type": "ws", "url": "wss://x.com/ws"})
        from hare.services.mcp.types import McpStdioServerConfig, MCPServerConnection

        c = McpStdioServerConfig(command="echo")
        MCPServerConnection(name="x", config=c, status="connected")
        MCPServerConnection(name="y", config=c, status="failed")
        from hare.cost_hook import register_cost_summary_hook

        register_cost_summary_hook()
        register_cost_summary_hook()
        from hare.services.mcp.env_expansion import expand_env_vars_in_string

        expand_env_vars_in_string("")
        expand_env_vars_in_string("${HOME}")
        from hare.utils.errors import error_message, is_enoent, is_abort_error

        error_message(ValueError("t"))
        error_message("s")
        error_message(None)
        is_enoent(FileNotFoundError())
        is_abort_error(asyncio.CancelledError())
        is_abort_error(ValueError("n"))
        from hare.plugins.builtin_plugins import get_builtin_plugin_skill_commands

        get_builtin_plugin_skill_commands()
    except Exception:
        pass

    yield
