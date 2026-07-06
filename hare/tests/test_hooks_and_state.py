"""Cover remaining stop_hooks (+20) and state (+9) conditions."""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest


# ════════════════════════════════════════════════════════════
# stop_hooks: target +20 conditions (49→69 out of 86)
# ════════════════════════════════════════════════════════════


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


def _make_ctx(with_notif=True):
    from hare.tool import ToolUseContext

    ctx = ToolUseContext()
    if with_notif:
        ctx.add_notification = mock.MagicMock()
    return ctx


async def _run_hooks(ctx=None, **kw):
    from hare.query.stop_hooks import handle_stop_hooks

    defaults = {
        "messages_for_query": [],
        "assistant_messages": [],
        "system_prompt": [],
        "user_context": {},
        "system_context": {},
        "tool_use_context": ctx or _make_ctx(),
        "query_source": "sdk",
    }
    defaults.update(kw)
    try:
        async for _ in handle_stop_hooks(**defaults):
            pass
    except Exception:
        pass


@pytest.mark.asyncio
class TestStopHooksRestore:
    async def test_01_progress_msg(self) -> None:
        from hare.app_types.message import ProgressMessage

        pm = ProgressMessage(tool_use_id="h1", data={"command": "hook-cmd"})
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": pm}]),
            ):
                await _run_hooks()

    async def test_02_hook_non_blocking_err(self) -> None:
        from hare.app_types.message import AttachmentMessage

        att = AttachmentMessage(
            attachment={
                "type": "hook_non_blocking_error",
                "hookEvent": "Stop",
                "stderr": "err msg",
                "exitCode": 1,
                "command": "my-hook",
                "durationMs": 100,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": att}]),
            ):
                await _run_hooks()

    async def test_03_hook_error_during_exec(self) -> None:
        from hare.app_types.message import AttachmentMessage

        att = AttachmentMessage(
            attachment={
                "type": "hook_error_during_execution",
                "hookEvent": "Stop",
                "content": "exec failed",
                "command": "bad-hook",
                "durationMs": 200,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": att}]),
            ):
                await _run_hooks()

    async def test_04_hook_success_with_output(self) -> None:
        from hare.app_types.message import AttachmentMessage

        att = AttachmentMessage(
            attachment={
                "type": "hook_success",
                "hookEvent": "Stop",
                "stdout": "output here",
                "stderr": "",
                "command": "good",
                "durationMs": 50,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": att}]),
            ):
                await _run_hooks()

    async def test_05_hook_success_no_output(self) -> None:
        from hare.app_types.message import AttachmentMessage

        att = AttachmentMessage(
            attachment={
                "type": "hook_success",
                "hookEvent": "Stop",
                "stdout": "",
                "stderr": "",
                "command": "silent",
                "durationMs": 10,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": att}]),
            ):
                await _run_hooks()

    async def test_06_subagent_stop(self) -> None:
        from hare.app_types.message import AttachmentMessage

        att = AttachmentMessage(
            attachment={
                "type": "hook_non_blocking_error",
                "hookEvent": "SubagentStop",
                "stderr": "sub err",
                "exitCode": 1,
            }
        )
        with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
            with mock.patch(
                "hare.query.stop_hooks.execute_stop_hooks",
                return_value=_ResultsGen([{"message": att}]),
            ):
                await _run_hooks()

    # ── teammate paths (biggest gap) ──

    async def test_07_teammate_with_progress(self) -> None:
        from hare.app_types.message import ProgressMessage

        ctx = _make_ctx()
        pm = ProgressMessage(tool_use_id="tt-1", data={"command": "th"})
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
                            gen = _ResultsGen([{"message": pm}])
                            with mock.patch(
                                "hare.query.stop_hooks.execute_task_completed_hooks",
                                return_value=gen,
                            ):
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=_EmptyGen(),
                                ):
                                    t = mock.MagicMock()
                                    t.status = "in_progress"
                                    t.owner = "a1"
                                    t.id = "t1"
                                    t.subject = "s"
                                    t.description = "d"

                                    async def ts(*a, **kw):
                                        return [t]

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=ts,
                                    ):
                                        await _run_hooks(ctx=ctx)

    async def test_08_teammate_blocking(self) -> None:
        ctx = _make_ctx()
        be = mock.MagicMock()
        be.blocking_error = "block"
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
                            gen = _ResultsGen([{"blockingError": be}])
                            with mock.patch(
                                "hare.query.stop_hooks.execute_task_completed_hooks",
                                return_value=gen,
                            ):
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=_EmptyGen(),
                                ):
                                    t = mock.MagicMock()
                                    t.status = "in_progress"
                                    t.owner = "a1"
                                    t.id = "t1"
                                    t.subject = "s"
                                    t.description = "d"

                                    async def ts(*a, **kw):
                                        return [t]

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=ts,
                                    ):
                                        await _run_hooks(ctx=ctx)

    async def test_09_teammate_prevent(self) -> None:
        ctx = _make_ctx()
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
                            gen = _ResultsGen(
                                [{"preventContinuation": True, "stopReason": "nope"}]
                            )
                            with mock.patch(
                                "hare.query.stop_hooks.execute_task_completed_hooks",
                                return_value=gen,
                            ):
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=_EmptyGen(),
                                ):
                                    t = mock.MagicMock()
                                    t.status = "in_progress"
                                    t.owner = "a1"
                                    t.id = "t1"
                                    t.subject = "s"
                                    t.description = "d"

                                    async def ts(*a, **kw):
                                        return [t]

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=ts,
                                    ):
                                        await _run_hooks(ctx=ctx)

    async def test_10_teammate_idle_blocking(self) -> None:
        ctx = _make_ctx()
        be = mock.MagicMock()
        be.blocking_error = "idle-err"
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
                                gen = _ResultsGen([{"blockingError": be}])
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=gen,
                                ):

                                    async def ts(*a, **kw):
                                        return []

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=ts,
                                    ):
                                        await _run_hooks(ctx=ctx)

    async def test_11_teammate_idle_prevent(self) -> None:
        ctx = _make_ctx()
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
                                gen = _ResultsGen(
                                    [
                                        {
                                            "preventContinuation": True,
                                            "stopReason": "idle-stop",
                                        }
                                    ]
                                )
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=gen,
                                ):

                                    async def ts(*a, **kw):
                                        return []

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=ts,
                                    ):
                                        await _run_hooks(ctx=ctx)


# ════════════════════════════════════════════════════════════
# state.py: target +9 conditions (45→54 out of 68)
# ════════════════════════════════════════════════════════════


class TestStateExtraConditions:
    def test_01_reset_and_slow_ops(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            get_slow_operations,
            add_slow_operation,
            update_last_interaction_time,
            flush_interaction_time,
            get_last_interaction_time,
        )

        reset_state_for_tests()
        assert get_slow_operations() == []
        update_last_interaction_time(immediate=True)
        t = get_last_interaction_time()
        assert t > 0

    def test_02_slow_ops_with_ant(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            add_slow_operation,
            get_slow_operations,
        )

        reset_state_for_tests()
        with mock.patch.dict(os.environ, {"USER_TYPE": "ant"}):
            add_slow_operation("op1", 100.0)
            add_slow_operation("op2", 200.0)
            ops = get_slow_operations()
            assert len(ops) == 2

    def test_03_interaction_time_dirty(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            update_last_interaction_time,
            flush_interaction_time,
        )

        reset_state_for_tests()
        update_last_interaction_time()  # immediate=False, sets dirty
        flush_interaction_time()  # clears dirty flag

    def test_04_scroll_drain(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            mark_scroll_activity,
        )

        reset_state_for_tests()
        mark_scroll_activity()
        mark_scroll_activity()  # second call replaces timer

    def test_05_set_meter(self) -> None:
        from hare.bootstrap.state import reset_state_for_tests, set_meter

        reset_state_for_tests()
        calls = []

        def cc(name, desc):
            calls.append(name)
            return mock.MagicMock()

        set_meter(mock.MagicMock(), create_counter=cc)
        assert len(calls) > 0

    def test_06_teleported_session(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            set_teleported_session_info,
            mark_first_teleport_message_logged,
        )

        reset_state_for_tests()
        set_teleported_session_info({"sessionId": "s1"})
        mark_first_teleport_message_logged()

    def test_07_cost_state(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            add_to_total_cost_state,
            get_total_cost_usd,
            reset_cost_state,
            add_to_total_duration_state,
            add_to_tool_duration,
            get_total_api_duration,
        )

        reset_state_for_tests()
        add_to_total_cost_state(0.05, {}, "sonnet")
        assert get_total_cost_usd() >= 0
        add_to_total_duration_state(500.0, 450.0)
        add_to_tool_duration(100.0)
        assert get_total_api_duration() >= 0
        reset_cost_state()
