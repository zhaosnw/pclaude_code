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


def test_cancelled_background_task_propagates_and_prunes_cleanly() -> None:
    """Bug 2: agent_tool.py's `_run_background()` closure wraps the child
    engine's message loop in `except Exception: pass` before calling
    record_completion() unconditionally. asyncio.CancelledError is a
    BaseException (not Exception, since Python 3.8), so it is NOT caught by
    that handler — it propagates straight out of `_run_background()`,
    skipping record_completion() entirely.

    Two things must hold once a background subagent task is cancelled
    (process shutdown, explicit cancellation, etc.):
      1. CancelledError must correctly propagate so `task.cancelled()` is
         True afterward — swallowing it into a no-op would break
         cooperative cancellation for anything awaiting/gathering this task.
      2. The registry must not get stuck thinking a cancelled task is still
         pending forever (has_pending() must settle back to False and the
         Task object must be pruned — see Bug 1's pruning logic)."""
    import hare.query.core as core
    import hare.query.deps as deps
    from hare.bootstrap.state import set_session_persistence_disabled
    from hare.query.deps import QueryDeps
    from hare.tools_impl.AgentTool import async_agent_tasks as aat

    started = asyncio.Event()

    async def call_model(payload: Any, *a: Any, **k: Any) -> Any:
        started.set()
        await asyncio.Event().wait()  # hang until this task is cancelled
        yield {}  # pragma: no cover - unreachable; keeps this an async generator

    orig = deps.production_deps

    def patched() -> QueryDeps:
        d = orig()
        return QueryDeps(
            call_model=call_model,
            microcompact=d.microcompact,
            autocompact=d.autocompact,
            uuid=d.uuid,
        )

    deps.production_deps = patched
    core.production_deps = patched
    set_session_persistence_disabled(True)
    aat.reset()

    async def scenario() -> None:
        tool = _AgentTool()
        ctx = ToolUseContext(options=ToolUseContextOptions())
        await tool.call(
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
        assert len(aat._registry.tasks) == 1
        task = aat._registry.tasks[0]

        # Let the background task actually reach (and hang inside) the
        # model call before we cancel it.
        await asyncio.wait_for(started.wait(), timeout=5.0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.cancelled(), (
            "the background task must correctly report itself as cancelled, "
            "not silently complete as if CancelledError were swallowed"
        )
        # A genuinely-cancelled run should not produce a <task-notification>
        # for a parent that is (most likely) shutting down too.
        assert aat._registry.completions == []
        # Registry hygiene: the cancelled task must not be reported as
        # pending forever, and must not be retained once observed done.
        assert aat.has_pending() is False
        assert aat._registry.tasks == []

    try:
        asyncio.run(scenario())
    finally:
        deps.production_deps = orig
        core.production_deps = orig
        set_session_persistence_disabled(False)
        aat.reset()
