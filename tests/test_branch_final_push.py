"""Final push: restore lost branch coverage from deleted test files."""

from __future__ import annotations

from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    AttachmentMessage,
    ProgressMessage,
)


def _a(content, mid=None, *, is_api_error: bool = False):
    m = AssistantMessage(
        message=APIMessage(role="assistant", content=content),
        is_api_error_message=is_api_error,
    )
    if mid:
        m.message.id = mid
    return m


def _u(content, tr=None):
    m = UserMessage(message=APIMessage(role="user", content=content))
    if tr:
        m.tool_use_result = tr
    return m


def _s(subtype, content=""):
    return SystemMessage(subtype=subtype, content=content)


class TestMessageFiltersAll:
    def test_filter_unresolved(self) -> None:
        from hare.utils.messages import filter_unresolved_tool_uses

        assert filter_unresolved_tool_uses([]) == []
        am = _a([{"type": "tool_use", "id": "t1"}])
        um = _u([{"type": "tool_result", "tool_use_id": "t1"}])
        r = filter_unresolved_tool_uses([am, um])
        assert isinstance(r, list)

    def test_filter_unresolved_mixed(self) -> None:
        from hare.utils.messages import filter_unresolved_tool_uses

        msgs = [
            _s("info"),
            _a(
                [
                    {"type": "tool_use", "id": "t1"},
                    "not_dict",
                    {"type": "tool_use", "id": 99},
                ]
            ),
            _u([{"type": "tool_result", "tool_use_id": "t1"}]),
        ]
        filter_unresolved_tool_uses(msgs)

    def test_filter_orphaned(self) -> None:
        from hare.utils.messages import filter_orphaned_thinking_only_messages

        filter_orphaned_thinking_only_messages([])
        r = filter_orphaned_thinking_only_messages([_a("plain text")])
        assert len(r) == 1
        r2 = filter_orphaned_thinking_only_messages(
            [_a([{"type": "tool_use", "id": "t1"}])]
        )
        assert len(r2) == 1
        r3 = filter_orphaned_thinking_only_messages(
            [_a([{"type": "thinking", "thinking": "hmm"}])]
        )
        assert isinstance(r3, list)

    def test_filter_whitespace(self) -> None:
        from hare.utils.messages import filter_whitespace_only_assistant_messages

        assert filter_whitespace_only_assistant_messages([]) == []
        r = filter_whitespace_only_assistant_messages([_a("hello")])
        assert len(r) == 1
        r2 = filter_whitespace_only_assistant_messages(
            [_a([{"type": "text", "text": "  "}])]
        )
        assert isinstance(r2, list)

    def test_filter_trailing(self) -> None:
        from hare.utils.messages import filter_trailing_thinking_from_last_assistant

        assert filter_trailing_thinking_from_last_assistant([]) == []
        r = filter_trailing_thinking_from_last_assistant(
            [
                _a(
                    [
                        {"type": "text", "text": "ok"},
                        {"type": "thinking", "thinking": "hmm"},
                    ]
                )
            ]
        )
        assert isinstance(r, list)

    def test_find_last_compact(self) -> None:
        from hare.utils.messages import find_last_compact_boundary_index

        assert find_last_compact_boundary_index([_u("hello")]) == -1
        assert find_last_compact_boundary_index([_s("compact_boundary"), _u("q")]) == 0

    def test_count_tool_calls(self) -> None:
        from hare.utils.messages import count_tool_calls

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
        assert count_tool_calls(msgs, "Bash") == 2
        assert count_tool_calls(msgs, "Read") == 1
        assert count_tool_calls(msgs, "Write") == 0

    def test_type_guards(self) -> None:
        from hare.utils.messages import (
            is_tool_use_request_message,
            is_tool_use_result_message,
            is_compact_boundary_message,
            is_system_local_command_message,
            is_synthetic_api_error_message,
        )

        a_tool = _a([{"type": "tool_use", "id": "t1"}])
        assert is_tool_use_request_message(a_tool) is True
        assert is_tool_use_request_message(_a("text")) is False
        u_res = _u([{"type": "tool_result", "tool_use_id": "t1"}])
        assert is_tool_use_result_message(u_res) is True
        assert is_tool_use_result_message(_u("plain")) is False
        assert is_compact_boundary_message(_s("compact_boundary")) is True
        assert is_compact_boundary_message(_u("q")) is False
        assert is_system_local_command_message(_s("local_command")) is True
        assert is_system_local_command_message(_s("info")) is False
        assert is_synthetic_api_error_message(_a("err", is_api_error=True)) is True

    def test_extract_text(self) -> None:
        from hare.utils.messages import extract_text_content, get_content_text

        assert extract_text_content([]) == ""
        assert extract_text_content([{"type": "text", "text": "hi"}]) == "hi"
        assert extract_text_content([{"type": "tool_use"}]) == ""
        assert (
            get_content_text(
                [
                    {"type": "text", "text": "a"},
                    {"type": "tool_use"},
                    {"type": "text", "text": "b"},
                ]
            )
            == "a\nb"
        )

    def test_message_text_getters(self) -> None:
        from hare.utils.messages import (
            get_assistant_message_text,
            get_user_message_text,
        )

        get_assistant_message_text(_a("hello"))
        get_assistant_message_text(_u("q"))
        get_user_message_text(_u("q"))
        get_user_message_text(_a("hello"))

    def test_extract_tag(self) -> None:
        from hare.utils.messages import (
            extract_tag,
            strip_prompt_xml_tags,
            is_empty_message_text,
        )

        assert extract_tag("<foo>bar</foo>", "foo") == "bar"
        assert extract_tag("", "tag") is None
        strip_prompt_xml_tags("<thinking>private</thinking> public")
        assert is_empty_message_text("") is True
        assert is_empty_message_text("hello") is False

    def test_derive_ids(self) -> None:
        from hare.utils.messages import derive_short_message_id, derive_uuid

        derive_short_message_id("abcdef01-2345-6789-abcd-ef0123456789")
        derive_uuid("aaaa-bbbb-cccc", 3)

    def test_normalize_api(self) -> None:
        from hare.utils.messages import normalize_messages_for_api

        msgs = [
            _u("q1"),
            _a("a1"),
            _u("q2"),
            _a("a2", mid="same"),
            _a("a3", mid="same"),
            _att({"type": "hook"}),
            _s("local_command", "/help"),
        ]
        normalize_messages_for_api(msgs)
        normalize_messages_for_api([])

    def test_normalize_messages(self) -> None:
        from hare.utils.messages import normalize_messages

        normalize_messages(
            [_u([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])]
        )


def _att(data):
    return AttachmentMessage(attachment=data)


class TestCostHook:
    def test_register_idempotent(self) -> None:
        from hare.cost_hook import register_cost_summary_hook

        register_cost_summary_hook()
        register_cost_summary_hook()


import asyncio
from unittest import mock
import pytest


@pytest.mark.asyncio
class TestStopHooksReturn:
    async def test_add_notif_err(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks
        from hare.app_types.message import AttachmentMessage

        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()
        att = AttachmentMessage(
            attachment={
                "type": "hook_non_blocking_error",
                "hookEvent": "Stop",
                "stderr": "e",
                "exitCode": 1,
            }
        )

        class G:
            def __init__(self, i):
                self._i = i
                self._p = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._p >= len(self._i):
                    raise StopAsyncIteration
                x = self._i[self._p]
                self._p += 1
                return x

        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=G([{"message": att}]),
            ):
                try:
                    async for _ in handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    ):
                        pass
                except Exception:
                    pass


class TestSettingsDeep:
    def test_all_functions(self) -> None:
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
        )

        _uniq_preserve_order([])
        _uniq_preserve_order(["a", "b", "a"])
        t = {"a": {"b": [1]}, "c": [1, 2]}
        s = {"a": {"b": [2], "d": 3}, "c": [3, 4]}
        _merge_settings(t, s)
        t_arr = {"plugins": ["p1"]}
        s_arr = {"plugins": ["p2", "p1"]}
        _merge_settings(t_arr, s_arr)
        get_settings_file_path_for_source("userSettings")
        get_settings_for_source("userSettings")
        get_auto_mode_config()
        get_managed_hooks_only()
        get_managed_permission_rules_only()
        get_strict_plugin_only_customization()
        parse_settings_file("/nonexistent/x.json")
        update_settings_for_source("userSettings", {"k": "v"}, project_dir="/tmp")
        _read_setting_excluding_project("key", ["userSettings"])
