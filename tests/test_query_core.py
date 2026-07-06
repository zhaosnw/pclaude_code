"""
Integration-style tests for `hare.query.core.query` (Python port of `query.ts`).

Strategy: inject ``QueryDeps`` with deterministic ``call_model`` / compaction
fakes so the loop runs without network or real GrowthBook. Assertions target
terminal reasons and stable yield shapes aligned with ``src/query.ts``.

**Covered here:** ``completed``, ``max_turns``, ``aborted_streaming``,
``model_error``, compaction hooks invoked, ``on_transition`` includes
``next_turn`` after a tool round.

**Still to add (same harness, more fakes):** ``blocking_limit``, ``image_error``,
``prompt_too_long``, ``stop_hook_prevented``, ``hook_stopped``, ``aborted_tools``,
``stop_hook_blocking`` (via stop-hook stubs), token-budget / reactive-compact /
collapse-drain paths (need ``feature()`` env and/or more deps on the Python
side once those modules are injectable), and ``FallbackTriggeredError`` retry
when a small ``FallbackTriggeredError`` type is shared from the API layer.
"""

from __future__ import annotations

import pytest

from hare.query.core import QueryParams, query
from hare.query.query_test_helpers import (
    TerminalCapture,
    assistant_text_only,
    assistant_with_tool_use,
    allow_all_can_use_tool,
    drain_query,
    make_deps,
    make_tool_use_context,
)
from hare.app_types.message import RequestStartEvent

pytestmark = pytest.mark.alignment


@pytest.mark.asyncio
async def test_completed_single_turn_text_response() -> None:
    async def call_model(_payload):
        yield assistant_text_only("hello from stub model")

    cap = TerminalCapture()
    ctx = make_tool_use_context()
    deps = make_deps(call_model=call_model)
    out = await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["You are a test assistant."],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=ctx,
                query_source="sdk",
                deps=deps,
                on_terminal=cap.on_terminal,
            )
        )
    )
    assert any(isinstance(x, RequestStartEvent) for x in out)
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"
    assert any(getattr(x, "type", None) == "assistant" for x in out)


@pytest.mark.asyncio
async def test_max_turns_stops_after_first_tool_round() -> None:
    """When max_turns=1, the loop must not schedule a second API turn after tools."""

    async def call_model(_payload):
        yield assistant_with_tool_use(tool_id="toolu_maxturns_1")

    cap = TerminalCapture()
    ctx = make_tool_use_context()
    deps = make_deps(call_model=call_model)
    out = await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["sys"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=ctx,
                query_source="sdk",
                max_turns=1,
                deps=deps,
                on_terminal=cap.on_terminal,
            )
        )
    )
    assert cap.terminal is not None
    assert cap.terminal.reason == "max_turns"
    assert cap.terminal.turn_count is not None
    assert cap.terminal.turn_count > 1
    assert any(
        getattr(x, "type", None) == "attachment"
        and (getattr(x, "attachment", {}) or {}).get("type") == "max_turns_reached"
        for x in out
    )


@pytest.mark.asyncio
async def test_aborted_streaming_after_model_finishes() -> None:
    ctx = make_tool_use_context(aborted=True)
    cap = TerminalCapture()

    async def call_model(_payload):
        yield assistant_text_only("partial before abort")

    out = await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["sys"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=ctx,
                query_source="sdk",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
            )
        )
    )
    assert cap.terminal is not None
    assert cap.terminal.reason == "aborted_streaming"
    assert any(getattr(x, "type", None) == "assistant" for x in out)


@pytest.mark.asyncio
async def test_model_error_surfaces_terminal_and_synthetic_results() -> None:
    async def call_model(_payload):
        if False:
            yield assistant_text_only("x")
        raise RuntimeError("simulated transport failure")

    cap = TerminalCapture()
    out = await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["sys"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
            )
        )
    )
    assert cap.terminal is not None
    assert cap.terminal.reason == "model_error"
    assert cap.terminal.error is not None
    assert any(getattr(x, "type", None) == "assistant" for x in out)


@pytest.mark.asyncio
async def test_deps_microcompact_and_autocompact_are_invoked() -> None:
    calls: list[str] = []

    async def call_model(_payload):
        yield assistant_text_only("ok")

    async def micro(messages, *_a, **_k):
        calls.append("micro")
        return {"messages": list(messages)}

    async def autocompact(*_a, **_k):
        calls.append("auto")
        return {}

    deps = make_deps(call_model=call_model)
    deps.microcompact.side_effect = micro
    deps.autocompact.side_effect = autocompact

    cap = TerminalCapture()
    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["sys"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                deps=deps,
                on_terminal=cap.on_terminal,
            )
        )
    )
    assert "micro" in calls and "auto" in calls
    assert deps.microcompact.await_count >= 1
    assert deps.autocompact.await_count >= 1
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_on_transition_next_turn_after_tool_round() -> None:
    transitions: list[str] = []
    _phase = {"n": 0}

    async def call_model(_payload):
        _phase["n"] += 1
        if _phase["n"] == 1:
            yield assistant_with_tool_use(tool_id="toolu_tr_1")
        else:
            yield assistant_text_only("second turn text-only completion")

    def on_transition(c):
        transitions.append(c.reason)

    cap = TerminalCapture()
    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["sys"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                max_turns=5,
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
                on_transition=on_transition,
            )
        )
    )
    assert "next_turn" in transitions
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"
