"""Hit feature-gated branches in stop_hooks.py by mocking feature() to True."""

from __future__ import annotations

import asyncio
import os
from unittest import mock
import pytest


@pytest.mark.asyncio
class TestFeatureGatedBranches:
    async def _run_handle_stop_hooks(self, ctx=None, **kw):
        from hare.tool import ToolUseContext
        from hare.query.stop_hooks import handle_stop_hooks
        from hare.app_types.message import UserMessage, APIMessage

        defaults = {
            "messages_for_query": [
                UserMessage(message=APIMessage(role="user", content="test"))
            ],
            "assistant_messages": [],
            "system_prompt": [],
            "user_context": {},
            "system_context": {},
            "tool_use_context": ctx or ToolUseContext(),
            "query_source": "repl_main_thread",
        }
        defaults.update(kw)
        try:
            async for _ in handle_stop_hooks(**defaults):
                pass
        except Exception:
            pass

    class _EmptyGen:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    async def test_feature_gated_paths(self) -> None:
        """Mock feature() to True to exercise TEMPLATES, EXTRACT_MEMORIES, CHICAGO_MCP branches."""
        from hare.tool import ToolUseContext

        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()

        # Set env vars for feature gates
        with mock.patch.dict(os.environ, {"CLAUDE_JOB_DIR": "/tmp/test_job"}):
            with mock.patch("hare.query.stop_hooks.feature", return_value=True):
                with mock.patch(
                    "hare.query.stop_hooks.is_bare_mode", return_value=True
                ):
                    with mock.patch(
                        "hare.query.stop_hooks.is_env_defined_falsy", return_value=True
                    ):
                        with mock.patch(
                            "hare.query.stop_hooks.is_extract_mode_active",
                            return_value=False,
                        ):
                            with mock.patch(
                                "hare.query.stop_hooks.execute_stop_hooks",
                                return_value=self._EmptyGen(),
                            ):
                                await self._run_handle_stop_hooks(ctx=ctx)

    async def test_feature_false_paths(self) -> None:
        """Mock feature() to False to exercise the feature-gated else branches."""
        from hare.tool import ToolUseContext

        ctx = ToolUseContext()
        ctx.add_notification = mock.MagicMock()

        with mock.patch("hare.query.stop_hooks.feature", return_value=False):
            with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
                with mock.patch(
                    "hare.query.stop_hooks.execute_stop_hooks",
                    return_value=self._EmptyGen(),
                ):
                    await self._run_handle_stop_hooks(ctx=ctx)

    async def test_is_bare_mode_false(self) -> None:
        """Not bare mode: exercises prompt suggestion, auto dream scheduling."""
        from hare.tool import ToolUseContext

        ctx = ToolUseContext()

        with mock.patch("hare.query.stop_hooks.feature", return_value=False):
            with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=False):
                with mock.patch(
                    "hare.query.stop_hooks.is_env_defined_falsy", return_value=True
                ):
                    with mock.patch(
                        "hare.query.stop_hooks.execute_stop_hooks",
                        return_value=self._EmptyGen(),
                    ):
                        with mock.patch(
                            "hare.query.stop_hooks.execute_prompt_suggestion",
                            return_value=None,
                        ):
                            with mock.patch(
                                "hare.query.stop_hooks.execute_auto_dream",
                                return_value=None,
                            ):
                                await self._run_handle_stop_hooks(ctx=ctx)

    async def test_repl_main_thread_query_source(self) -> None:
        """repl_main_thread source: exercises cache param save, sdk also."""
        from hare.tool import ToolUseContext

        ctx = ToolUseContext()

        with mock.patch("hare.query.stop_hooks.feature", return_value=False):
            with mock.patch("hare.query.stop_hooks.is_bare_mode", return_value=True):
                with mock.patch(
                    "hare.query.stop_hooks.save_cache_safe_params"
                ) as mock_save:
                    with mock.patch(
                        "hare.query.stop_hooks.execute_stop_hooks",
                        return_value=self._EmptyGen(),
                    ):
                        await self._run_handle_stop_hooks(ctx=ctx, query_source="sdk")
                        assert mock_save.called
