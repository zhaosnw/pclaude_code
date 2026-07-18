"""Regression coverage for the background-subagent task registry's pruning
behavior — hare/tools_impl/AgentTool/async_agent_tasks.py.

`_registry.tasks` is append-only at register_background_task() time; nothing
previously removed a finished asyncio.Task from it, so a long session that
dispatches many Agent(run_in_background=true) subagents would leak every
completed Task (and transitively its result/exception object graph) forever,
and has_pending()/wait_for_next_completion()'s linear scans over that list
would grow more expensive with every dispatch. See hare/tools_impl/AgentTool/
async_agent_tasks.py's _prune_done_tasks() for the fix these tests pin.

This file is intentionally under top-level tests/, not hare/tests/ (the
legacy mirror tree that predates the canonical tests/ split and is not
gated by `make test`/`make alignment-guardrails`/CI) — see
docs/alignment-status/2026-07-06-demirroring-checklist.md for that split's
background. Naming follows the tests/test_fork_subagent.py convention:
one top-level tests/test_<module>.py per hare/tools_impl/... module under
direct unit test, no "hare_" prefix.
"""

from __future__ import annotations

import asyncio

from hare.tools_impl.AgentTool import async_agent_tasks as aat
from hare.tools_impl.AgentTool.async_agent_tasks import AsyncAgentCompletion


def setup_function() -> None:
    aat.reset()


def teardown_function() -> None:
    aat.reset()


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
