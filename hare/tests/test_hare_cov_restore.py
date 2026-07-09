from __future__ import annotations

import asyncio
import os
import tempfile
from unittest import mock
import pytest

from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    AttachmentMessage,
    ProgressMessage,
)


def _a(content, mid=None, is_err=False):
    m = AssistantMessage(
        message=APIMessage(role="assistant", content=content),
        is_api_error_message=is_err,
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


def _att(data=None):
    return AttachmentMessage(attachment=data or {"type": "test"})


class TestFilterAll:
    def test_compact_boundary(self) -> None:
        from hare.utils.messages import find_last_compact_boundary_index

        assert find_last_compact_boundary_index([_u("h")]) == -1
        assert find_last_compact_boundary_index([_s("compact_boundary"), _u("q")]) == 0

    def test_unresolved(self) -> None:
        from hare.utils.messages import filter_unresolved_tool_uses

        assert filter_unresolved_tool_uses([]) == []
        am = _a(
            [
                {"type": "tool_use", "id": "t1"},
                "not_dict",
                {"type": "tool_use", "id": 99},
            ]
        )
        um = _u(
            [
                {"type": "tool_result", "tool_use_id": "t1"},
                {"type": "tool_result", "tool_use_id": 456},
            ]
        )
        filter_unresolved_tool_uses([_s("info"), am, um])

    def test_orphaned(self) -> None:
        from hare.utils.messages import filter_orphaned_thinking_only_messages

        filter_orphaned_thinking_only_messages([])
        r = filter_orphaned_thinking_only_messages([_a("text")])
        assert len(r) == 1
        filter_orphaned_thinking_only_messages([_a([{"type": "tool_use", "id": "t1"}])])

    def test_whitespace(self) -> None:
        from hare.utils.messages import filter_whitespace_only_assistant_messages

        assert filter_whitespace_only_assistant_messages([]) == []
        filter_whitespace_only_assistant_messages([_a("hello")])
        filter_whitespace_only_assistant_messages(
            [_a([{"type": "text", "text": "  "}])]
        )

    def test_trailing(self) -> None:
        from hare.utils.messages import filter_trailing_thinking_from_last_assistant

        assert filter_trailing_thinking_from_last_assistant([]) == []
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

    def test_guards(self) -> None:
        from hare.utils.messages import (
            is_tool_use_request_message,
            is_tool_use_result_message,
            is_compact_boundary_message,
            is_system_local_command_message,
            is_synthetic_api_error_message,
        )

        is_tool_use_request_message(_a([{"type": "tool_use", "id": "t1"}]))
        is_tool_use_request_message(_u("q"))
        is_tool_use_result_message(_u([{"type": "tool_result", "tool_use_id": "t1"}]))
        is_tool_use_result_message(_u("plain"))
        is_compact_boundary_message(_s("compact_boundary"))
        is_system_local_command_message(_s("local_command"))
        is_synthetic_api_error_message(_a("err", is_err=True))

    def test_text(self) -> None:
        from hare.utils.messages import (
            extract_text_content,
            get_content_text,
            get_assistant_message_text,
            get_user_message_text,
        )

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
        get_user_message_text(_u("q"))

    def test_xml(self) -> None:
        from hare.utils.messages import (
            extract_tag,
            strip_prompt_xml_tags,
            is_empty_message_text,
        )

        extract_tag("<foo>bar</foo>", "foo")
        extract_tag("", "tag")
        strip_prompt_xml_tags("<thinking>x</thinking> y")
        is_empty_message_text("")
        is_empty_message_text("hello")

    def test_ids(self) -> None:
        from hare.utils.messages import derive_short_message_id, derive_uuid

        derive_short_message_id("abcdef01-2345-6789-abcd-ef0123456789")
        derive_uuid("aaaa-bbbb-cccc", 3)

    def test_api_pipeline(self) -> None:
        from hare.utils.messages import normalize_messages_for_api, normalize_messages

        normalize_messages_for_api([_u("q"), _a("a"), _u("q2")])
        normalize_messages_for_api([])
        normalize_messages(
            [_u([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])]
        )


class _EG:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _G:
    def __init__(self, items):
        self._i = items
        self._p = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._p >= len(self._i):
            raise StopAsyncIteration
        x = self._i[self._p]
        self._p += 1
        return x


@pytest.mark.asyncio
class TestHooks:
    async def test_err(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks

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
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_G([{"message": att}]),
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


class TestSettings:
    def test_all(self) -> None:
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
        t = {"a": {"b": [1]}}
        s = {"a": {"b": [2], "d": 3}}
        _merge_settings(t, s)
        get_settings_file_path_for_source("userSettings")
        get_settings_for_source("userSettings")
        get_auto_mode_config()
        get_managed_hooks_only()
        get_managed_permission_rules_only()
        get_strict_plugin_only_customization()
        parse_settings_file("/nonexistent/x.json")
        with tempfile.TemporaryDirectory(prefix="hare-settings-") as tmpdir:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = tmpdir
            config_dir = os.path.join(tmpdir, ".hare")
            os.environ["HARE_CONFIG_DIR"] = config_dir
            os.environ["CLAUDE_CONFIG_DIR"] = config_dir
            try:
                update_settings_for_source("userSettings", {"k": "v"}, project_dir="/tmp")
                _read_setting_excluding_project("key", ["userSettings"])
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
                os.environ.pop("HARE_CONFIG_DIR", None)
                os.environ.pop("CLAUDE_CONFIG_DIR", None)


class TestMisc:
    def test_types(self) -> None:
        from hare.services.mcp.types import McpStdioServerConfig, MCPServerConnection

        c = McpStdioServerConfig(command="echo")
        MCPServerConnection(name="x", config=c, status="connected")
        MCPServerConnection(name="y", config=c, status="failed")

    def test_prompt(self) -> None:
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

    def test_cost(self) -> None:
        from hare.cost_hook import register_cost_summary_hook

        register_cost_summary_hook()
        register_cost_summary_hook()

    def test_plugins(self) -> None:
        from hare.plugins.builtin_plugins import get_builtin_plugin_skill_commands

        assert isinstance(get_builtin_plugin_skill_commands(), list)

    def test_env(self) -> None:
        from hare.services.mcp.env_expansion import expand_env_vars_in_string

        expand_env_vars_in_string("")
        expand_env_vars_in_string("${HOME}")

    def test_errors(self) -> None:
        from hare.utils.errors import error_message, is_enoent, is_abort_error

        error_message(ValueError("t"))
        error_message("s")
        error_message(None)
        is_enoent(FileNotFoundError())
        is_abort_error(asyncio.CancelledError())
        is_abort_error(ValueError("n"))
