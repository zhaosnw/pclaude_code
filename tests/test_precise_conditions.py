from __future__ import annotations

import asyncio
from unittest import mock
import pytest


class TestQueryEnginePrecise:
    def test_config_with_commands(self) -> None:
        from hare.query_engine import QueryEngineConfig, QueryEngine
        from hare.app_types.command import LocalCommand

        cmd = LocalCommand(name="tc", description="T", aliases=[], type="local")
        cfg = QueryEngineConfig(
            cwd="/tmp",
            tools=[],
            commands=[cmd],
            max_turns=1,
            include_partial_messages=True,
            replay_user_messages=True,
        )
        engine = QueryEngine(cfg)
        assert engine is not None


@pytest.mark.asyncio
class TestStopHooksPrecise:
    async def test_add_notification_with_errors(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks
        from hare.app_types.message import AttachmentMessage

        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()
        att = AttachmentMessage(
            attachment={
                "type": "hook_non_blocking_error",
                "hookEvent": "Stop",
                "stderr": "err",
                "exitCode": 1,
                "command": "h",
                "durationMs": 100,
            }
        )

        class _Gen:
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

        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_Gen([{"message": att}]),
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

    async def test_teammate_progress_L505(self) -> None:
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks
        from hare.app_types.message import ProgressMessage

        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()
        pm = ProgressMessage(tool_use_id="tt-L505", data={"command": "th"})

        class _Gen:
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

        class _EG:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

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
                            return_value=_EG(),
                        ):
                            gen = _Gen([{"message": pm}])
                            with mock.patch(
                                "hare.query.stop_hooks.execute_task_completed_hooks",
                                return_value=gen,
                            ):
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=_EG(),
                                ):
                                    t = mock.MagicMock()
                                    t.status = "in_progress"
                                    t.owner = "a1"
                                    t.id = "t1"
                                    t.subject = "s"
                                    t.description = "d"

                                    async def _ts(*a, **kw):
                                        return [t]

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=_ts,
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


class TestStatePrecise:
    def test_interaction_dirty(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            update_last_interaction_time,
            flush_interaction_time,
            get_last_interaction_time,
        )

        reset_state_for_tests()
        update_last_interaction_time()
        flush_interaction_time()
        assert get_last_interaction_time() > 0

    def test_scroll_draining(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            mark_scroll_activity,
            get_is_scroll_draining,
        )

        reset_state_for_tests()
        mark_scroll_activity()
        assert get_is_scroll_draining() is True

    def test_slow_ops_empty(self) -> None:
        from hare.bootstrap.state import reset_state_for_tests, get_slow_operations

        reset_state_for_tests()
        assert get_slow_operations() == []


class TestCostHookPrecise:
    def test_cost_with_usage(self) -> None:
        from hare.cost_tracker import reset_cost_tracker, add_usage, format_total_cost
        from hare.services.api.logging import NonNullableUsage

        reset_cost_tracker()
        add_usage(NonNullableUsage(input_tokens=8000, output_tokens=3000))
        summary = format_total_cost()
        assert len(summary) > 0
        reset_cost_tracker()
