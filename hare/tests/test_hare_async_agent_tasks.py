"""Unit tests for the background subagent task registry and notification."""

from __future__ import annotations

import asyncio

from hare.tools_impl.AgentTool import async_agent_tasks as aat
from hare.tools_impl.AgentTool.async_agent_tasks import AsyncAgentCompletion


def setup_function() -> None:
    aat.reset()


def teardown_function() -> None:
    aat.reset()


def test_record_and_drain_completion() -> None:
    assert not aat.has_pending()
    aat.record_completion(
        AsyncAgentCompletion(
            agent_id="a1",
            tool_use_id="a1",
            description="task",
            result_text="did it",
        )
    )
    assert aat.has_pending()
    drained = aat.drain_completions()
    assert len(drained) == 1
    assert drained[0].result_text == "did it"
    assert not aat.has_pending()


def test_build_task_notification_shape() -> None:
    notice = aat.build_task_notification(
        AsyncAgentCompletion(
            agent_id="agent-9",
            tool_use_id="agent-9",
            description="make a file",
            result_text="Subagent done.",
            subagent_tokens=80,
            tool_uses=1,
            duration_ms=971,
        )
    )
    # Matches the release's re-entry envelope captured from 2.1.209.
    assert notice.startswith("[SYSTEM NOTIFICATION - NOT USER INPUT]")
    assert "<task-notification>" in notice
    assert "<task-id>agent-9</task-id>" in notice
    assert '<summary>Agent "make a file" finished</summary>' in notice
    assert "<result>Subagent done.</result>" in notice
    assert "<subagent_tokens>80</subagent_tokens>" in notice


def test_wait_for_next_completion_returns_queued() -> None:
    aat.record_completion(
        AsyncAgentCompletion(
            agent_id="a2", tool_use_id="a2", description="t", result_text="r"
        )
    )
    got = asyncio.run(aat.wait_for_next_completion(timeout=1.0))
    assert got is not None
    assert got.agent_id == "a2"


def test_wait_returns_none_when_nothing_pending() -> None:
    got = asyncio.run(aat.wait_for_next_completion(timeout=0.1))
    assert got is None


# ---------------------------------------------------------------------------
# Bug 1 — `_registry.tasks` must be pruned of finished tasks, not grow
# without bound over a long session of background subagent dispatches.
# ---------------------------------------------------------------------------


def test_has_pending_prunes_completed_tasks() -> None:
    """Once a background task is done() and its completion (if any) has been
    recorded, the Task object itself has nothing further to give the
    registry — has_pending() must drop it instead of holding onto it
    forever."""

    async def _noop() -> None:
        return None

    async def scenario() -> None:
        tasks = [asyncio.ensure_future(_noop()) for _ in range(5)]
        for t in tasks:
            aat.register_background_task(t)
        # Let every task actually finish before we ask the registry about it.
        await asyncio.gather(*tasks)
        assert len(aat._registry.tasks) == 5, "not pruned until observed"

        # has_pending() must prune finished tasks as a side effect of the read.
        assert aat.has_pending() is False
        assert aat._registry.tasks == [], (
            "finished tasks must be dropped from the registry once observed "
            "done, not retained indefinitely"
        )

    asyncio.run(scenario())


def test_wait_for_next_completion_prunes_completed_tasks() -> None:
    """wait_for_next_completion() is the other read path over _registry.tasks
    (query_engine.py's drain loop) — it must prune too, not just has_pending()."""

    async def _noop() -> None:
        return None

    async def scenario() -> None:
        tasks = [asyncio.ensure_future(_noop()) for _ in range(3)]
        for t in tasks:
            aat.register_background_task(t)
        await asyncio.gather(*tasks)

        got = await aat.wait_for_next_completion(timeout=0.1)
        assert got is None  # nothing was ever record_completion()'d
        assert aat._registry.tasks == [], (
            "wait_for_next_completion() must prune finished tasks it observes"
        )

    asyncio.run(scenario())


def test_registry_tasks_do_not_grow_unbounded_across_many_dispatches() -> None:
    """Simulates a long session: many background dispatches, each completing
    (and recording its completion) before the next is registered — mirroring
    agent_tool.py's _run_background(), which always calls record_completion()
    itself before its task finishes. _registry.tasks must stay bounded by the
    number of currently-in-flight tasks (here: 0 or 1), never by the total
    number of dispatches ever made."""

    async def _finish_and_record(n: int) -> None:
        aat.record_completion(
            AsyncAgentCompletion(
                agent_id=f"a{n}", tool_use_id=f"a{n}", description="t", result_text="r"
            )
        )

    async def scenario() -> None:
        for n in range(50):
            t = asyncio.ensure_future(_finish_and_record(n))
            aat.register_background_task(t)
            await t
            # Draining a queued completion is the trigger the real drain loop
            # uses; has_pending()/drain_completions() must have pruned the
            # now-finished task by the time the next one is registered.
            aat.has_pending()
            aat.drain_completions()
            assert len(aat._registry.tasks) <= 1, (
                f"registry.tasks grew to {len(aat._registry.tasks)} after "
                f"{n + 1} dispatches — completed tasks are not being pruned"
            )

    asyncio.run(scenario())
