"""Request-side E2E for the Agent (subagent) tool.

A fixture-replay *output* golden can't verify subagent behavior: the model
output is fixed regardless of whether a child query loop actually ran. So this
drives hare in-process with a *capturing* call_model (injected through
production_deps, like test_request_side_alignment) that records every model
call — parent AND child — in order, and scripts:

    call 0 (parent) -> Agent tool_use(prompt=<child prompt>)
    call 1 (child)  -> end_turn text (the subagent's result)
    call 2 (parent) -> end_turn text (final answer)

That lets us assert the real contract the TS Agent tool implements:
  * spawning Agent actually runs a *nested* model call (3 calls, not 1) —
    i.e. the subagent is not a stub that fabricates a "completed" result;
  * the child's first request carries the subagent prompt;
  * the parent's continuation receives the child's output as the tool_result.
"""

from __future__ import annotations

import asyncio
import json

import pytest


def _drive_subagent(subagent_type: str | None = None) -> list[dict]:
    """Run a single parent turn that spawns one subagent; return the ordered
    list of payloads passed to the model (parent turn 1, child turn 1,
    parent turn 2). Optionally pin the subagent_type."""
    calls: list[dict] = []

    spawn_input: dict = {"description": "do a thing", "prompt": "SUBAGENT_PROMPT_MARKER"}
    if subagent_type is not None:
        spawn_input["subagent_type"] = subagent_type
    PARENT_SPAWN = {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_sub",
                "name": "Agent",
                "input": spawn_input,
            }
        ],
        "stop_reason": "tool_use",
    }
    CHILD_RESULT = {
        "content": [{"type": "text", "text": "CHILD_RESULT_MARKER"}],
        "stop_reason": "end_turn",
    }
    PARENT_FINAL = {
        "content": [{"type": "text", "text": "parent done"}],
        "stop_reason": "end_turn",
    }
    # Any call beyond the script returns a clean end_turn so an unexpected extra
    # call can't hang the loop — it surfaces as a call-count assertion failure.
    script = [PARENT_SPAWN, CHILD_RESULT, PARENT_FINAL]
    SAFETY = {"content": [{"type": "text", "text": "stop"}], "stop_reason": "end_turn"}

    def _make():
        async def call_model(payload, *a, **k):
            i = len(calls)
            calls.append(payload)
            r = script[i] if i < len(script) else SAFETY
            yield {
                "type": "assistant",
                "content": r["content"],
                "stop_reason": r["stop_reason"],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        return call_model

    import hare.query.core as core
    import hare.query.deps as deps
    from hare.bootstrap.state import set_session_persistence_disabled
    from hare.query.deps import QueryDeps

    orig = deps.production_deps

    def patched():
        d = orig()
        return QueryDeps(
            call_model=_make(),
            microcompact=d.microcompact,
            autocompact=d.autocompact,
            uuid=d.uuid,
        )

    deps.production_deps = patched
    core.production_deps = patched
    set_session_persistence_disabled(True)
    try:

        async def run():
            from hare.sdk import HareClient, HareClientOptions
            from hare.utils.cwd import get_cwd

            c = await HareClient.create(HareClientOptions(cwd=get_cwd()))
            async for _ in c.stream("delegate this to a subagent"):
                pass

        asyncio.run(run())
    finally:
        deps.production_deps = orig
        core.production_deps = orig
        set_session_persistence_disabled(False)
    return calls


def _messages_json(payload: dict) -> str:
    return json.dumps(payload.get("messages", []), default=str)


@pytest.mark.integration
def test_spawning_agent_runs_a_nested_model_call():
    """The Agent tool must run a real child query loop, not fabricate a result.
    Parent spawn + child turn + parent continuation == 3 model calls."""
    calls = _drive_subagent()
    assert len(calls) == 3, (
        f"expected 3 model calls (parent spawn, child, parent continuation); "
        f"got {len(calls)} — subagent likely did not run a nested query loop"
    )


@pytest.mark.integration
def test_child_request_carries_the_subagent_prompt():
    """The second (child) model call's request must contain the prompt the
    parent passed to Agent(prompt=...)."""
    calls = _drive_subagent()
    assert len(calls) >= 2
    assert "SUBAGENT_PROMPT_MARKER" in _messages_json(calls[1]), (
        "child request did not carry the subagent prompt"
    )


@pytest.mark.integration
def test_parent_continuation_receives_child_result():
    """After the subagent finishes, the parent's next request must include the
    child's output (delivered as the Agent tool_result)."""
    calls = _drive_subagent()
    assert len(calls) >= 3
    assert "CHILD_RESULT_MARKER" in _messages_json(calls[2]), (
        "parent continuation did not receive the subagent's result"
    )


def _child_system_prompt(calls: list[dict]) -> str:
    sp = calls[1].get("system_prompt")
    return "\n\n".join(sp) if isinstance(sp, list) else str(sp)


@pytest.mark.integration
def test_child_uses_dedicated_subagent_system_prompt():
    """The TS Agent tool gives the subagent the built-in agent's dedicated system
    prompt, NOT the main interactive-loop prompt. The general-purpose agent's
    prompt opens with 'You are an agent for Claude Code' and asks for a concise
    report; the main loop opens with 'You are an interactive agent'."""
    calls = _drive_subagent()
    sp = _child_system_prompt(calls)
    assert "You are an agent for Claude Code" in sp, (
        "child should use the general-purpose subagent prompt"
    )
    assert "interactive agent that helps users with software engineering" not in sp, (
        "child must not use the main interactive-loop system prompt"
    )


def _child_tool_names(calls: list[dict]) -> set:
    tools = calls[1].get("tools") or []
    names = set()
    for t in tools:
        n = t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
        if n:
            names.add(n)
    return names


@pytest.mark.integration
def test_explore_subagent_is_read_only_and_uses_explore_prompt():
    """Spawning subagent_type='Explore' must resolve to the Explore built-in
    agent: a READ-ONLY tool set (no Edit/Write/NotebookEdit) and the Explore
    system prompt — not the full tool set + general-purpose/main-loop prompt."""
    calls = _drive_subagent(subagent_type="Explore")
    assert len(calls) == 3, f"expected nested subagent call, got {len(calls)}"

    sp = _child_system_prompt(calls)
    assert "file search specialist for Claude Code" in sp, (
        "Explore child should use the Explore dedicated prompt"
    )
    assert "READ-ONLY MODE" in sp

    names = _child_tool_names(calls)
    assert names, "child request carried no tools"
    for forbidden in ("Edit", "Write", "NotebookEdit"):
        assert forbidden not in names, (
            f"Explore subagent must not have {forbidden}; tools={sorted(names)}"
        )
