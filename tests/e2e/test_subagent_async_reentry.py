"""E2E coverage for the background-subagent (Agent(run_in_background=true))
re-entry path in QueryEngine.submit_message() (hare/query_engine.py).

The only existing coverage of this path before this file was the fixture
replay golden at hare/alignment/cases/subagent/async_dispatch/case.json,
which only exercises a plain-text final response after the
<task-notification> re-entry — it never scripts a tool_use in the re-entry
turn, so it cannot catch a dangling tool_use bug, nor does it exercise
multiple re-entries or a slow-but-still-running background task.

Like test_subagent_request_side.py, this drives hare in-process with a
*capturing* call_model (injected through production_deps) that records every
model call — parent AND child — in order, and a fully scripted response
sequence. That lets us assert on the real transcript/message-list shape the
re-entry loop produces, not just the final text.
"""

from __future__ import annotations

import asyncio
import json

import pytest


def _make_call_model(script, calls):
    """Build a call_model that returns script[i] for the i-th call overall
    (across parent AND any child/subagent engines — they share one counter,
    since they share one event loop), falling back to a safe end_turn text
    for any call beyond the scripted sequence so an unexpected extra call
    can't hang the test — it shows up as a call-count assertion failure
    instead.

    A script entry is normally a single response dict (one content block,
    one AssistantMessage yielded for that call). It may instead be a *list*
    of response dicts to simulate a real streaming turn with multiple
    content blocks (e.g. a text preamble followed by a tool_use): the real
    client (hare/services/api/client.py's _streaming_request_events) yields
    one *separate* AssistantMessage per content_block_stop for a single
    logical turn — see
    test_streaming_request_events_yields_per_block_not_cumulative in
    hare/tests/test_hare_api_client_streaming.py — so a list entry yields
    each block dict in turn, all from the one call_model invocation (one
    entry in `calls`), matching that shape."""
    SAFETY = {
        "content": [{"type": "text", "text": "stop"}],
        "stop_reason": "end_turn",
    }

    async def call_model(payload, *a, **k):
        i = len(calls)
        calls.append(payload)
        r = script[i] if i < len(script) else SAFETY
        blocks = r if isinstance(r, list) else [r]
        for block in blocks:
            yield {
                "type": "assistant",
                "content": block["content"],
                "stop_reason": block["stop_reason"],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    return call_model


def _text(s):
    return {"content": [{"type": "text", "text": s}], "stop_reason": "end_turn"}


def _tool_use(tool_use_id, name, input):
    return {
        "content": [
            {"type": "tool_use", "id": tool_use_id, "name": name, "input": input}
        ],
        "stop_reason": "tool_use",
    }


def _text_preamble_block(s):
    """A non-final content block within a multi-block turn (see
    _make_call_model's list-entry support) — mirrors an in-progress
    streaming block, which carries no stop_reason yet."""
    return {"content": [{"type": "text", "text": s}], "stop_reason": None}


def _drive(script, prompts, *, max_turns=None):
    """Run `prompts` in order against a single HareClient wired to a scripted
    call_model. Returns (calls, client) so tests can inspect both the raw
    model-call payloads (in order) and the engine's final message list."""
    calls: list[dict] = []

    import hare.query.core as core
    import hare.query.deps as deps
    from hare.bootstrap.state import set_session_persistence_disabled
    from hare.query.deps import QueryDeps

    call_model = _make_call_model(script, calls)
    orig = deps.production_deps

    def patched():
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

    from hare.tools_impl.AgentTool import async_agent_tasks as aat

    aat.reset()
    client_holder: dict = {}
    try:

        async def run():
            from hare.sdk import HareClient, HareClientOptions
            from hare.utils.cwd import get_cwd

            c = await HareClient.create(
                HareClientOptions(cwd=get_cwd(), max_turns=max_turns)
            )
            client_holder["client"] = c
            for prompt in prompts:
                async for _ in c.stream(prompt):
                    pass

        asyncio.run(run())
    finally:
        deps.production_deps = orig
        core.production_deps = orig
        set_session_persistence_disabled(False)
        aat.reset()
    return calls, client_holder["client"]


def _is_parent_call(payload: dict) -> bool:
    """A model-call payload came from the top-level engine (not a spawned
    subagent) iff options.agent_id is unset — QueryEngineConfig.agent_id is
    only set for a subagent's child engine (AgentTool.call), and it flows
    straight through to the request payload (query/core.py _stream_model_turn)."""
    return (payload.get("options") or {}).get("agent_id") is None


def _dangling_tool_use_ids(messages) -> list[str]:
    """Return tool_use ids from assistant messages that are not immediately
    (before the next assistant turn) answered by a matching tool_result in a
    following user message. Mirrors what the Anthropic API itself requires:
    every tool_use must be followed by its tool_result before the next
    assistant turn."""
    dangling: list[str] = []
    for i, msg in enumerate(messages):
        if getattr(msg, "type", None) != "assistant":
            continue
        content = msg.message.content
        if not isinstance(content, list):
            continue
        tool_use_ids = {
            b.get("id")
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        if not tool_use_ids:
            continue
        satisfied: set = set()
        for later in messages[i + 1 :]:
            if getattr(later, "type", None) == "assistant":
                break
            if getattr(later, "type", None) != "user":
                continue
            lc = later.message.content
            if not isinstance(lc, list):
                continue
            for b in lc:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    satisfied.add(b.get("tool_use_id"))
        dangling.extend(tid for tid in tool_use_ids if tid not in satisfied)
    return dangling


# ---------------------------------------------------------------------------
# Bug 1 — dangling tool_use when the parent's re-entry response is itself a
# tool_use, not plain text.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reentry_tool_use_gets_a_matching_tool_result():
    """Script: parent dispatches a background Agent, the child finishes, the
    parent's <task-notification> re-entry response is a tool_use (an unknown
    tool, so it resolves deterministically without side effects) rather than
    plain text. Before the fix, query_engine.py's re-entry loop dropped the
    "user"-type tool_result message entirely — self._mutable_messages ends up
    with a tool_use block that is never answered, which the Anthropic API
    rejects on the next real turn."""
    script = [
        _tool_use(
            "t_spawn",
            "Agent",
            {
                "description": "bg",
                "prompt": "CHILD_PROMPT",
                "run_in_background": True,
            },
        ),  # 0: parent main loop turn 1
        _text("Launched it."),  # 1: parent main loop turn 2 (post-launch continuation)
        _text("CHILD_RESULT_MARKER"),  # 2: child's own turn 1
        _tool_use("t_reentry", "NoSuchToolXYZ", {}),  # 3: parent re-entry turn 1
        _text("Handled the notification."),  # 4: parent re-entry turn 2
    ]
    calls, client = _drive(script, ["delegate this to a background agent"])

    parent_calls = [c for c in calls if _is_parent_call(c)]
    # 4 parent-visible turns: spawn, post-launch continuation, re-entry
    # tool_use, re-entry continuation. If the re-entry loop had stalled or
    # dropped messages such that query() never got to make its second
    # internal call, this would come up short.
    assert len(parent_calls) == 4, (
        f"expected 4 parent model calls, got {len(parent_calls)}: "
        f"{[c.get('messages') for c in parent_calls]}"
    )

    dangling = _dangling_tool_use_ids(client.engine._mutable_messages)
    assert not dangling, (
        f"dangling tool_use id(s) with no tool_result in transcript: {dangling}"
    )

    # Belt and suspenders: drive one more *real* user turn and confirm its
    # request payload is well-formed — no synthetic "[Tool result missing due
    # to internal error]" repair, which is what query/core.py's
    # ensure_tool_result_pairing() would have had to paper over here if the
    # real tool_result had been lost from the persisted history.
    from hare.utils.messages import SYNTHETIC_TOOL_RESULT_PLACEHOLDER

    calls2, _ = _drive(
        [
            _tool_use(
                "t_spawn",
                "Agent",
                {
                    "description": "bg",
                    "prompt": "CHILD_PROMPT",
                    "run_in_background": True,
                },
            ),
            _text("Launched it."),
            _text("CHILD_RESULT_MARKER"),
            _tool_use("t_reentry", "NoSuchToolXYZ", {}),
            _text("Handled the notification."),
            _text("Sure, all good."),  # second real user turn
        ],
        ["delegate this to a background agent", "thanks, what's next?"],
    )
    last_payload = calls2[-1]
    serialized = json.dumps(last_payload.get("messages", []), default=str)
    assert SYNTHETIC_TOOL_RESULT_PLACEHOLDER not in serialized, (
        "next real user turn required a synthetic tool_result repair — the "
        "real tool_result was lost from the persisted transcript"
    )


# ---------------------------------------------------------------------------
# Bug 2 — max_turns must be a conversation-wide cap, not re-granted in full
# on every re-entry.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_max_turns_is_not_inflated_across_multiple_reentries():
    """Two background dispatches chained through two separate re-entry
    passes. Each individual query() invocation only ever needs 1-2 turns, so
    a per-call budget would never notice the bug — only a conversation-wide
    cap does. With max_turns=4 and 2+2+1=5 turns' worth of scripted parent
    work, the fix must stop before the 5th parent turn; the pre-fix behavior
    (each re-entry re-granted the full max_turns=4) would let all 5 through."""
    script = [
        _tool_use(
            "t_spawn1",
            "Agent",
            {
                "description": "bg1",
                "prompt": "A",
                "run_in_background": True,
            },
        ),  # 0: parent main loop turn 1
        _text("Launched first."),  # 1: parent main loop turn 2
        _text("childA done"),  # 2: child A turn 1
        _tool_use(
            "t_spawn2",
            "Agent",
            {
                "description": "bg2",
                "prompt": "B",
                "run_in_background": True,
            },
        ),  # 3: parent re-entry pass 1, turn 1
        _text("Launched second."),  # 4: parent re-entry pass 1, turn 2
        _text("childB done"),  # 5: child B turn 1 (should NOT be reached)
        _text("All done."),  # 6: parent re-entry pass 2 (should NOT be reached)
    ]
    calls, client = _drive(script, ["delegate work"], max_turns=4)

    parent_calls = [c for c in calls if _is_parent_call(c)]
    assert len(parent_calls) <= 4, (
        f"max_turns=4 was not enforced across re-entries: "
        f"{len(parent_calls)} parent turns consumed"
    )
    # Pin the exact expected count too (not just the bound), so a future
    # change that starves the budget *too* early also gets caught.
    assert len(parent_calls) == 4


@pytest.mark.integration
def test_multi_block_turn_counts_as_one_turn_not_one_per_block():
    """A single logical turn with a text preamble + a tool_use block streams
    as TWO separate AssistantMessage yields from the real client (see
    _make_call_model's docstring and
    test_streaming_request_events_yields_per_block_not_cumulative) — but it
    is still only one real query()-internal turn. Counting yielded
    assistant messages (instead of the query()-internal turn-boundary
    signal) would treat it as two, shrinking the re-entry's max_turns
    budget twice as fast as it should.

    max_turns=3, and the real turn cost is exactly 2 (main loop) + 1
    (re-entry) = 3:
      - main loop turn 1 (2 content blocks: text + Agent bg-dispatch
        tool_use) -> 1 real turn
      - main loop turn 2 (post-launch continuation, plain text)   -> 1 real
        turn
      - re-entry turn 1 (plain text, answering the notification)  -> 1 real
        turn, needs exactly the 1 turn of budget left over (3 - 2 = 1)

    Miscounting main loop turn 1 as 2 (one per block) leaves only 3-3=0
    turns of budget for the re-entry, and the budget-exhausted check would
    then skip the re-entry's query() call entirely — the notification would
    never be drained and the final response would still be "Launched it.",
    not "Handled it.". With correct counting, the re-entry gets its 1 turn
    and completes normally."""
    script = [
        [
            _text_preamble_block("I'll dispatch this in the background."),
            _tool_use(
                "t_spawn",
                "Agent",
                {
                    "description": "bg",
                    "prompt": "A",
                    "run_in_background": True,
                },
            ),
        ],  # 0: parent main loop turn 1 — TWO content blocks, ONE real turn
        _text("Launched it."),  # 1: parent main loop turn 2
        _text("child done"),  # 2: child turn 1
        _text("Handled it."),  # 3: parent re-entry turn 1
    ]
    calls, client = _drive(script, ["delegate this to a background agent"], max_turns=3)

    parent_calls = [c for c in calls if _is_parent_call(c)]
    assert len(parent_calls) == 3, (
        f"expected all 3 real parent turns to run within max_turns=3, got "
        f"{len(parent_calls)} parent calls — a multi-block turn is likely "
        f"still being over-counted as more than one turn"
    )

    result = None
    for msg in reversed(client.engine._mutable_messages):
        if getattr(msg, "type", None) == "assistant":
            content = msg.message.content
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        result = b.get("text")
                        break
            if result:
                break
    assert result == "Handled it.", (
        f"expected the re-entry's response to have run (final text "
        f"'Handled it.'), got {result!r} — the re-entry was likely skipped "
        f"because the multi-block main-loop turn was miscounted as 2 turns, "
        f"leaving no budget for the re-entry"
    )


# ---------------------------------------------------------------------------
# Bug 3 — a wait_for_next_completion() timeout while a task is still running
# must not be treated the same as "nothing left pending".
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_completion_survives_a_mid_poll_timeout():
    """wait_for_next_completion() has an internal 30s timeout and can return
    None either because nothing is pending (loop should stop) or because a
    background task simply hasn't finished within this poll window (loop
    must keep waiting). We can't wait out a real 30s timeout in a test, so we
    fake exactly that ambiguous return: the *first* call returns None (as if
    the internal timeout fired) while the background task is still legitimately
    running; subsequent calls fall through to the real implementation. Before
    the fix, a single None reply of either kind caused an unconditional
    break, silently dropping the eventual completion for this
    submit_message() call."""
    from hare.tools_impl.AgentTool import async_agent_tasks as aat

    real_wait = aat.wait_for_next_completion
    call_count = {"n": 0}

    async def fake_wait(timeout=30.0):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate the internal 30s timeout elapsing with the task still
            # running — i.e. a real None-but-still-pending return, without
            # actually waiting 30 real seconds.
            return None
        return await real_wait(timeout=timeout)

    script = [
        _tool_use(
            "t_spawn",
            "Agent",
            {
                "description": "bg",
                "prompt": "A",
                "run_in_background": True,
            },
        ),  # 0: parent main loop turn 1
        _text("Launched it."),  # 1: parent main loop turn 2
        _text("child done"),  # 2: child turn 1
        _text("Handled the notification."),  # 3: parent re-entry turn 1
    ]

    aat.wait_for_next_completion = fake_wait
    try:
        calls, client = _drive(script, ["delegate this"])
    finally:
        aat.wait_for_next_completion = real_wait

    assert call_count["n"] >= 2, (
        "fix should re-poll wait_for_next_completion() instead of breaking "
        "on a None reply while a task is still pending"
    )

    parent_calls = [c for c in calls if _is_parent_call(c)]
    assert len(parent_calls) == 3, (
        "the notification for the still-running-at-first-poll task was "
        f"dropped: expected 3 parent calls (spawn, post-launch, re-entry), "
        f"got {len(parent_calls)}"
    )

    # The final result must reflect the re-entry response, not the earlier
    # "Launched it." — proving the notification for the slow completion
    # actually made it through instead of being silently dropped.
    result = None
    for msg in reversed(client.engine._mutable_messages):
        if getattr(msg, "type", None) == "assistant":
            content = msg.message.content
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        result = b.get("text")
                        break
            if result:
                break
    assert result == "Handled the notification."


# ---------------------------------------------------------------------------
# Bug 4 — the child engine's AgentId (what SubagentStop fires against) and
# the background-completion id (what the parent sees in the "Async agent
# launched" result and the eventual <task-notification>/<task-id>) must be
# the SAME identity for one logical subagent dispatch.
# ---------------------------------------------------------------------------


def test_subagent_stop_hook_agent_id_matches_task_notification_id():
    """agent_tool.py's run_in_background branch used to mint TWO independent
    uuid4() values for what is logically a single subagent run: one became
    the child QueryEngine's AgentId (config.agent_id), which is what
    execute_stop_hooks keys off to fire SubagentStop instead of a
    main-session Stop; the other, generated separately, became the
    background-completion's agent_id/tool_use_id — the value reported back
    to the parent as "agentId: ..." in the launched-tool result and later
    embedded in the <task-id>/<tool-use-id> of the <task-notification>
    re-entry message. Anything trying to correlate "which subagent just
    stopped" (the hook) with "which subagent's background task just
    completed" (the notification the parent model is told to SendMessage
    to) saw two unrelated ids for the same run.

    This test registers a real SubagentStop hook callback (through the same
    AsyncHookRegistry.register() path exercised by
    tests/test_tool_hooks_runtime.py) to capture the agent_id the hook fires
    with, drives a background Agent dispatch to completion, and asserts that
    id matches both the "agentId: <id>" text in the launched-tool result and
    the <task-id> embedded in the resulting <task-notification>."""
    import re

    from hare.utils.hooks import get_hook_registry

    registry = get_hook_registry()
    captured_agent_ids: list = []

    def _capture_subagent_stop(context: dict) -> dict:
        captured_agent_ids.append(context.get("agent_id"))
        return {}

    registry.register(
        "SubagentStop", "test-capture-subagent-stop", _capture_subagent_stop,
        source="test",
    )
    try:
        script = [
            _tool_use(
                "t_spawn",
                "Agent",
                {
                    "description": "bg",
                    "prompt": "CHILD_PROMPT",
                    "run_in_background": True,
                },
            ),  # 0: parent main loop turn 1
            _text("Launched it."),  # 1: parent main loop turn 2
            _text("CHILD_RESULT_MARKER"),  # 2: child's own turn 1 -- its
            # teardown is what fires SubagentStop, captured above.
            _text("Handled the notification."),  # 3: parent re-entry turn 1
        ]
        calls, client = _drive(script, ["delegate this to a background agent"])
    finally:
        registry.unregister("SubagentStop", "test-capture-subagent-stop")

    assert len(captured_agent_ids) == 1, (
        f"expected exactly one SubagentStop firing, got {captured_agent_ids}"
    )
    subagent_stop_agent_id = captured_agent_ids[0]
    assert subagent_stop_agent_id

    # Find the "Async agent launched" tool_result for the spawn tool_use and
    # pull the id out of its "agentId: <id> (internal ID ..." text.
    launched_text = None
    for msg in client.engine._mutable_messages:
        if getattr(msg, "type", None) != "user":
            continue
        content = msg.message.content
        if not isinstance(content, list):
            continue
        for b in content:
            if (
                isinstance(b, dict)
                and b.get("type") == "tool_result"
                and b.get("tool_use_id") == "t_spawn"
            ):
                launched_text = b.get("content")
    assert launched_text, "no tool_result found for the spawn tool_use (t_spawn)"
    launched_match = re.search(r"agentId: (\S+) \(", launched_text)
    assert launched_match, f"no 'agentId: ...' found in launched text: {launched_text!r}"
    launched_agent_id = launched_match.group(1)

    # Find the <task-notification> re-entry message and pull out <task-id>.
    notification_text = None
    for msg in client.engine._mutable_messages:
        if getattr(msg, "type", None) != "user":
            continue
        content = msg.message.content
        if isinstance(content, str) and "<task-notification>" in content:
            notification_text = content
    assert notification_text, "no <task-notification> message found in transcript"
    task_id_match = re.search(r"<task-id>(.*?)</task-id>", notification_text)
    assert task_id_match, f"no <task-id> found in notification: {notification_text!r}"
    notification_task_id = task_id_match.group(1)

    assert subagent_stop_agent_id == launched_agent_id == notification_task_id, (
        "the id the SubagentStop hook fired with must be the SAME id the "
        "parent was told to use to address this agent — got "
        f"SubagentStop agent_id={subagent_stop_agent_id!r}, "
        f"launched agentId={launched_agent_id!r}, "
        f"task-notification task-id={notification_task_id!r}"
    )


# ---------------------------------------------------------------------------
# Bug 5 — a background subagent task that is cancelled mid-flight (process
# shutdown, explicit cancellation, etc.) must correctly propagate
# CancelledError and leave the async-task registry in a clean state, not a
# silently-lost or permanently-"pending" one.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cancelled_background_task_propagates_and_prunes_cleanly():
    """agent_tool.py's `_run_background()` closure wraps the child engine's
    message loop in `except Exception: pass` before calling
    record_completion() unconditionally. asyncio.CancelledError is a
    BaseException (not Exception, since Python 3.8), so cancelling this
    background asyncio.Task must not be silently swallowed into a no-op —
    task.cancelled() must end up True, and async_agent_tasks' registry must
    not get stuck treating a cancelled task as pending forever (see that
    module's _prune_done_tasks(), added alongside this fix).

    Unlike the other tests in this file, this one doesn't use `_drive()`'s
    scripted call_model (a fixed response list can't model "hang until
    cancelled") — it drives `_AgentTool.call()` directly with a call_model
    that blocks on an asyncio.Event so cancellation timing is deterministic,
    reusing the same production_deps-patching approach `_drive()` uses."""
    import hare.query.core as core
    import hare.query.deps as deps
    from hare.bootstrap.state import set_session_persistence_disabled
    from hare.query.deps import QueryDeps
    from hare.tool import ToolUseContext, ToolUseContextOptions
    from hare.tools_impl.AgentTool import async_agent_tasks as aat
    from hare.tools_impl.AgentTool.agent_tool import _AgentTool

    started = asyncio.Event()

    async def call_model(payload, *a, **k):
        started.set()
        await asyncio.Event().wait()  # hang until this task is cancelled
        yield {}  # pragma: no cover - unreachable; keeps this an async generator

    orig = deps.production_deps

    def patched():
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
