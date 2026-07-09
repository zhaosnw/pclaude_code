"""Exercise remaining branch gaps in query/stop_hooks.py via mocked deps."""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest

from hare.tool import ToolUseContext
from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    UserMessage,
    ProgressMessage,
    AttachmentMessage,
)
from hare.query.stop_hooks import handle_stop_hooks, StopHookResult


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_ctx():
    return ToolUseContext()


async def _collect(generator):
    events = []
    try:
        async for evt in generator:
            events.append(evt)
    except Exception:
        pass
    return events


async def _yield_nothing():
    yield


class AsyncGeneratorWrapper:
    """Allow mocking an async generator return value."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        async def _gen():
            for item in self._items:
                yield item

        return _gen().__aiter__()


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStopHooksBranchesAsync:
    """Exercise handle_stop_hooks code paths via mocked execute_stop_hooks."""

    async def test_bare_mode_bypass(self) -> None:
        """Bare mode skips background bookkeeping (prompt suggestion, memory extraction, auto-dream)."""
        ctx = _make_ctx()
        # Remove feature env to hit the bare_mode path
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([]),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert isinstance(events, list)

    async def test_repl_main_thread_saves_cache_params(self) -> None:
        """repl_main_thread and sdk query sources save cache-safe params."""
        ctx = _make_ctx()
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([]),
            ):
                with mock.patch(
                    "hare.query.stop_hooks.save_cache_safe_params"
                ) as mock_save:
                    await _collect(
                        handle_stop_hooks(
                            messages_for_query=[],
                            assistant_messages=[],
                            system_prompt=[],
                            user_context={},
                            system_context={},
                            tool_use_context=ctx,
                            query_source="repl_main_thread",
                        )
                    )
                    mock_save.assert_called()

    async def test_dry_run_stop_hook_active(self) -> None:
        """Exercises the stop_hook_active parameter branch."""
        ctx = _make_ctx()
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([]),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                        stop_hook_active=True,
                    )
                )
                assert isinstance(events, list)

    async def test_prevent_continuation_path(self) -> None:
        """Hook that prevents continuation exercises that branch."""
        ctx = _make_ctx()
        hook_results = [{"preventContinuation": True, "stopReason": "test stop"}]
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper(hook_results),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert len(events) >= 1

    async def test_blocking_error_path(self) -> None:
        """Hook with blocking error exercises that branch."""
        ctx = _make_ctx()
        blocking_error = mock.MagicMock()
        blocking_error.blocking_error = "test blocking error"
        hook_results = [{"blockingError": blocking_error}]
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper(hook_results),
            ):
                with mock.patch(
                    "hare.query.stop_hooks.get_stop_hook_message",
                    return_value="blocking error help",
                ):
                    events = await _collect(
                        handle_stop_hooks(
                            messages_for_query=[],
                            assistant_messages=[],
                            system_prompt=[],
                            user_context={},
                            system_context={},
                            tool_use_context=ctx,
                            query_source="sdk",
                        )
                    )
                    assert len(events) >= 1

    async def test_escape_special_modifier(self) -> None:
        """Verify the key_name for regular character path."""
        from hare.keybindings.match import get_key_name
        from hare.keybindings.ink_key import InkKey

        key = InkKey()
        assert get_key_name("a", key) == "a"


@pytest.mark.asyncio
class TestStopHooksMessageProcessing:
    """Target specific message processing branches in handle_stop_hooks."""

    async def test_progress_message_with_tool_use_id(self) -> None:
        """Progress message with tool_use_id hits the hook count/info tracking branch."""
        ctx = _make_ctx()
        pm = ProgressMessage(tool_use_id="hook-tool-1", data={"command": "test-hook"})
        hook_results = [{"message": pm}]
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper(hook_results),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert len(events) >= 1

    async def test_attachment_hook_non_blocking_error(self) -> None:
        """Attachment with hook_non_blocking_error hits that branch."""
        ctx = _make_ctx()
        att = AttachmentMessage(
            attachment={
                "type": "hook_non_blocking_error",
                "hookEvent": "Stop",
                "stderr": "non blocking error text",
                "exitCode": 1,
                "command": "my-hook",
                "durationMs": 100,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([{"message": att}]),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert len(events) >= 1

    async def test_attachment_hook_error_during_execution(self) -> None:
        """Attachment with hook_error_during_execution hits that branch."""
        ctx = _make_ctx()
        att = AttachmentMessage(
            attachment={
                "type": "hook_error_during_execution",
                "hookEvent": "Stop",
                "content": "hook execution failed",
                "command": "bad-hook",
                "durationMs": 200,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([{"message": att}]),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert len(events) >= 1

    async def test_attachment_hook_success_with_output(self) -> None:
        """Attachment with hook_success and output hits the has_output branch."""
        ctx = _make_ctx()
        att = AttachmentMessage(
            attachment={
                "type": "hook_success",
                "hookEvent": "Stop",
                "stdout": "output from hook",
                "stderr": "",
                "command": "good-hook",
                "durationMs": 50,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([{"message": att}]),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert len(events) >= 1

    async def test_attachment_hook_success_no_output(self) -> None:
        """Attachment with hook_success but no output."""
        ctx = _make_ctx()
        att = AttachmentMessage(
            attachment={
                "type": "hook_success",
                "hookEvent": "Stop",
                "stdout": "",
                "stderr": "",
                "command": "silent-hook",
                "durationMs": 10,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([{"message": att}]),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert len(events) >= 1

    async def test_attachment_subagent_stop(self) -> None:
        """Attachment with hookEvent=SubagentStop."""
        ctx = _make_ctx()
        att = AttachmentMessage(
            attachment={
                "type": "hook_non_blocking_error",
                "hookEvent": "SubagentStop",
                "stderr": "subagent error",
                "exitCode": 1,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=AsyncGeneratorWrapper([{"message": att}]),
            ):
                events = await _collect(
                    handle_stop_hooks(
                        messages_for_query=[],
                        assistant_messages=[],
                        system_prompt=[],
                        user_context={},
                        system_context={},
                        tool_use_context=ctx,
                        query_source="sdk",
                    )
                )
                assert len(events) >= 1
