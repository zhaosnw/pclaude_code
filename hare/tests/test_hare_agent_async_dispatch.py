"""AgentTool async dispatch — aligned with AgentTool.tsx:1328.

The released CLI runs a subagent in the background when run_in_background is
true: the Task call returns "Async agent launched" immediately instead of
blocking the parent until the subagent finishes. The interleaved response
stream this produces cannot be recorded by the positional-fixture golden
harness, so this behavior is pinned here instead.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hare.tools_impl.AgentTool.agent_tool import _AgentTool
from hare.tool import ToolUseContext, ToolUseContextOptions


def test_async_dispatch_returns_launched_without_blocking() -> None:
    """run_in_background=true returns an 'Async agent launched' result."""
    from hare.tools_impl.AgentTool import async_agent_tasks

    async_agent_tasks.reset()
    tool = _AgentTool()
    ctx = ToolUseContext(options=ToolUseContextOptions())

    async def go() -> Any:
        return await tool.call(
            {
                "description": "bg",
                "prompt": "do work",
                "subagent_type": "general-purpose",
                "run_in_background": True,
            },
            ctx,
            None,
            None,
        )

    result = asyncio.run(go())
    assert "Async agent launched successfully." in str(result.data)
    assert "agentId:" in str(result.data)
    # The parent must NOT receive the subagent's final text synchronously.
    assert "background" in str(result.data)
    # A background task was registered for QueryEngine to drain later.
    assert async_agent_tasks.has_pending()
    async_agent_tasks.reset()


def test_sync_dispatch_still_blocks_and_returns_result() -> None:
    """run_in_background=false keeps the original synchronous behavior."""
    tool = _AgentTool()
    ctx = ToolUseContext(options=ToolUseContextOptions())

    async def go() -> Any:
        return await tool.call(
            {
                "description": "sync",
                "prompt": "do work",
                "subagent_type": "general-purpose",
                "run_in_background": False,
            },
            ctx,
            None,
            None,
        )

    result = asyncio.run(go())
    # No launched-in-background envelope on the synchronous path.
    assert "Async agent launched" not in str(result.data)
