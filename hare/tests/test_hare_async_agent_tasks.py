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
