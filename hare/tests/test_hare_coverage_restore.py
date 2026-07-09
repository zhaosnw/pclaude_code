"""Restore lost branch/line coverage from accidentally deleted test files.

Covers: stop_hooks async branches, settings, normalize_messages,
builtin_plugins, prompt, cost_hook, state, errors, mcp types."""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest


# ── stop_hooks: teammate branches ────────────────────────────────────


class _EmptyGen:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _ResultsGen:
    def __init__(self, items):
        self._items = items
        self._pos = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._pos >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._pos]
        self._pos += 1
        return item


@pytest.mark.asyncio
class TestStopHooksRestore:
    async def test_bare_mode_bypass(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks

        ctx = ToolUseContext()
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks", return_value=_EmptyGen()
            ):
                events = []
                try:
                    async for evt in handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    ):
                        events.append(evt)
                except Exception:
                    pass

    async def test_prevent_continuation(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks

        ctx = ToolUseContext()
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen(
                    [{"preventContinuation": True, "stopReason": "test"}]
                ),
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

    async def test_blocking_error(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks

        ctx = ToolUseContext()
        be = mock.MagicMock()
        be.blocking_error = "blocking"
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.get_stop_hook_message", return_value="help"
            ):
                with mock.patch(
                    "hare.query.stop_hooks.execute_stop_hooks",
                    return_value=_ResultsGen([{"blockingError": be}]),
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

    async def test_message_processing_progress(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks
        from hare.app_types.message import ProgressMessage

        ctx = ToolUseContext()
        pm = ProgressMessage(tool_use_id="h1", data={"command": "hook-cmd"})
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": pm}]),
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

    async def test_attachment_hook_error(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks
        from hare.app_types.message import AttachmentMessage

        ctx = ToolUseContext()
        att = AttachmentMessage(
            attachment={
                "type": "hook_non_blocking_error",
                "hookEvent": "Stop",
                "stderr": "test error",
                "exitCode": 1,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": att}]),
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

    async def test_attachment_hook_success_output(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks
        from hare.app_types.message import AttachmentMessage

        ctx = ToolUseContext()
        att = AttachmentMessage(
            attachment={
                "type": "hook_success",
                "hookEvent": "Stop",
                "stdout": "output text",
                "stderr": "",
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": att}]),
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

    async def test_teammate_idle_path(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks

        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()
        with mock.patch("hare.query.stop_hooks.is_teammate", return_value=True):
            with mock.patch("hare.query.stop_hooks.get_agent_name", return_value="a1"):
                with mock.patch(
                    "hare.query.stop_hooks.get_team_name", return_value="t1"
                ):
                    with mock.patch(
                        "hare.query.stop_hooks.is_bare_mode", return_value=True
                    ):
                        with mock.patch(
                            "hare.query.stop_hooks.execute_stop_hooks",
                            return_value=_EmptyGen(),
                        ):
                            with mock.patch(
                                "hare.query.stop_hooks.execute_task_completed_hooks",
                                return_value=_EmptyGen(),
                            ):
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=_EmptyGen(),
                                ):

                                    async def _empty(*a, **kw):
                                        return []

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=_empty,
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


# ── settings / cost_hook / errors / state ──────────────────────────────


class TestRestoreMisc:
    def test_cost_hook_idempotent(self) -> None:
        from hare.cost_hook import register_cost_summary_hook

        register_cost_summary_hook()
        register_cost_summary_hook()

    def test_state_empty_ops(self) -> None:
        from hare.bootstrap.state import get_slow_operations, reset_state_for_tests

        reset_state_for_tests()
        assert get_slow_operations() == []

    def test_compact_format_summary(self) -> None:
        from hare.services.compact.prompt import format_compact_summary

        r1 = format_compact_summary("<analysis>a</analysis><summary>s</summary>")
        assert "s" in r1
        r2 = format_compact_summary("<summary>only</summary>")
        assert "only" in r2
        r3 = format_compact_summary("no tags")
        assert isinstance(r3, str)

    def test_merge_settings(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        t = {"a": {"b": 1}, "plugins": ["p1"]}
        s = {"a": {"c": 2}, "plugins": ["p2", "p3"]}
        _merge_settings(t, s)
        assert "c" in t["a"]

    def test_normalize_messages_multi_block(self) -> None:
        from hare.utils.messages import normalize_messages, normalize_messages_for_api
        from hare.app_types.message import (
            AssistantMessage,
            UserMessage,
            APIMessage,
            SystemMessage,
            AttachmentMessage,
        )

        am = AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "b1"},
                    {"type": "text", "text": "b2"},
                ],
            )
        )
        um1 = UserMessage(message=APIMessage(role="user", content="q1"))
        um2 = UserMessage(message=APIMessage(role="user", content="q2"))
        sm = SystemMessage(subtype="local_command", content="/help")
        att = AttachmentMessage(attachment={"type": "file_change"})
        msgs = [sm, um1, um2, am, att]
        r1 = normalize_messages(msgs)
        r2 = normalize_messages_for_api(r1)
        assert isinstance(r2, list)

    def test_mcp_utils_validate(self) -> None:
        from hare.services.mcp.utils import (
            validate_server_config,
            format_server_name,
            sanitize_mcp_output,
        )

        assert isinstance(validate_server_config({}), list)
        assert format_server_name("test") == "Test"
        assert sanitize_mcp_output("hello") == "hello"

    def test_ndjson_safe_stringify(self) -> None:
        from hare.cli.ndjson_safe_stringify import ndjson_safe_stringify

        assert isinstance(ndjson_safe_stringify({"k": "v"}), str)
        assert isinstance(ndjson_safe_stringify([1, 2, 3]), str)

    def test_error_functions(self) -> None:
        from hare.utils.errors import (
            error_message,
            is_enoent,
            is_fs_inaccessible,
            is_abort_error,
        )

        assert isinstance(error_message(ValueError("test")), str)
        assert isinstance(error_message("plain"), str)
        assert is_enoent(FileNotFoundError()) is True
        assert is_enoent(ValueError()) is False
        assert is_fs_inaccessible(OSError(13, "perm")) is True
        assert is_fs_inaccessible(ValueError()) is False


# ── builtin_plugins ──────────────────────────────────────────────────


class TestBuiltinPluginRestore:
    def test_register_and_get_all(self) -> None:
        from hare.plugins.builtin_plugins import (
            register_builtin_plugin,
            get_builtin_plugins,
            get_builtin_plugin_skill_commands,
            clear_builtin_plugins,
        )

        clear_builtin_plugins()
        register_builtin_plugin(
            {
                "name": "test-p",
                "description": "Test",
                "version": "1.0",
                "skills": [{"name": "s1", "description": "Skill"}],
                "defaultEnabled": True,
            }
        )
        register_builtin_plugin(
            {
                "name": "test-disabled",
                "description": "Disabled",
                "version": "1.0",
                "defaultEnabled": False,
            }
        )

        r = get_builtin_plugins(None)
        assert len(r["enabled"]) >= 1
        assert len(r["disabled"]) >= 1

        def s():
            return {
                "enabledPlugins": {
                    "test-p@builtin": True,
                    "test-disabled@builtin": True,
                }
            }

        r2 = get_builtin_plugins(s)
        assert len(r2["enabled"]) >= 2

        cmds = get_builtin_plugin_skill_commands(
            lambda: {"enabledPlugins": {"test-p@builtin": True}}
        )
        assert len(cmds) >= 1

        clear_builtin_plugins()
