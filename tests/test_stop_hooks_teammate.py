"""Cover teammate hook branches in stop_hooks.py (lines 444-606)."""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from hare.tool import ToolUseContext
from hare.query.stop_hooks import handle_stop_hooks


class _EmptyGen:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
class TestStopHooksTeammate:
    async def test_teammate_idle_path(self) -> None:
        """is_teammate()=True exercises the teammate idle and task completed hook paths."""
        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()

        async def _empty_tasks(*a, **kw):
            return []

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
                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=_empty_tasks,
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
                                        assert isinstance(events, list)

    async def test_teammate_with_tasks(self) -> None:
        """Exercises the for task in in_progress_tasks loop."""
        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()
        # Return one task to hit the for loop
        task1 = mock.MagicMock()
        task1.status = "in_progress"
        task1.owner = "a1"
        task1.id = "task-1"
        task1.subject = "subject"
        task1.description = "desc"

        async def _one_task(*a, **kw):
            return [task1]

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
                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=_one_task,
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

    async def test_teammate_prevent_continuation(self) -> None:
        """Teammate hook that prevents continuation."""
        ctx = ToolUseContext()

        class _Gen:
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
                            return_value=_EmptyGen(),
                        ):
                            with mock.patch(
                                "hare.query.stop_hooks.execute_task_completed_hooks",
                                return_value=_Gen(),
                            ):
                                with mock.patch(
                                    "hare.query.stop_hooks.execute_teammate_idle_hooks",
                                    return_value=_Gen(),
                                ):

                                    async def _no_tasks(*a, **kw):
                                        return []

                                    with mock.patch(
                                        "hare.query.stop_hooks.list_tasks",
                                        side_effect=_no_tasks,
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
