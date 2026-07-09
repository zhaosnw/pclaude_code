"""
Exhaustive behavioral tests for ``hare.query.core`` vs ``query.ts``.

Every ``finish("…")`` in ``core.py`` should have at least one test here (or in
``test_query_core.py``) documenting how it is reached. Where the production
code calls heavy subsystems (GrowthBook, collapse, reactive compact), tests
use ``unittest.mock.patch`` on the **Python** entrypoints — the assertions are
still about the query loop contract that mirrors TS.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from dataclasses import replace

from hare.query.core import QueryParams, query
from hare.query.transitions import Continue
from hare.query.query_test_helpers import (
    AbortDuringToolCall,
    FallbackTriggeredError,
    MutableAbortController,
    TerminalCapture,
    assistant_api_error_completed,
    assistant_text_only,
    assistant_with_tool_use,
    assistant_withheld_max_output_tokens,
    assistant_withheld_media_too_large,
    assistant_withheld_prompt_too_long,
    allow_all_can_use_tool,
    drain_query,
    make_deps,
    make_tool_use_context,
)
from hare.utils.image_validation import ImageSizeError, OversizedImage
from hare.utils.messages import create_attachment_message, create_user_message

pytestmark = pytest.mark.alignment


@pytest.mark.asyncio
async def test_terminal_blocking_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hare.query.core._is_at_blocking_limit",
        lambda _messages, _ctx=None: True,
    )
    cap = TerminalCapture()

    async def call_model(_p):
        yield assistant_text_only("ignored once blocked")

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert cap.terminal.reason == "blocking_limit"


@pytest.mark.asyncio
async def test_terminal_image_size_error_from_stream() -> None:
    cap = TerminalCapture()

    async def call_model(_p):
        raise ImageSizeError([OversizedImage(index=0, size=9_999_999)], 5_000_000)

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert cap.terminal.reason == "image_error"


@pytest.mark.asyncio
async def test_terminal_stop_hook_prevented() -> None:
    cap = TerminalCapture()
    hook_attachment = create_attachment_message(
        {"type": "hook_stopped_continuation", "reason": "test"}
    )

    async def call_model(_p):
        yield assistant_text_only("done")

    async def fake_collect(**_kwargs: Any) -> dict[str, Any]:
        return {
            "messages": [hook_attachment],
            "blocking_errors": [],
            "prevent_continuation": True,
        }

    with patch("hare.query.core._run_stop_hooks_collect", new_callable=AsyncMock) as m:
        m.side_effect = fake_collect
        await drain_query(
            query(
                QueryParams(
                    messages=[],
                    system_prompt=["s"],
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
    assert cap.terminal.reason == "stop_hook_prevented"


@pytest.mark.asyncio
async def test_transition_stop_hook_blocking_then_completed() -> None:
    """Blocking stop-hook injects meta user errors and loops; second turn completes."""
    cap = TerminalCapture()
    transitions: list[str] = []
    phase = {"n": 0}

    async def call_model(_p):
        phase["n"] += 1
        yield assistant_text_only(f"turn-{phase['n']}")

    block_user = create_user_message(
        content="[stop hook blocked]",
        is_meta=True,
    )
    hook_calls = [0]

    async def fake_collect(**_kwargs: Any) -> dict[str, Any]:
        hook_calls[0] += 1
        if hook_calls[0] == 1:
            return {
                "messages": [],
                "blocking_errors": [block_user],
                "prevent_continuation": False,
            }
        return {"messages": [], "blocking_errors": [], "prevent_continuation": False}

    def on_tr(c):
        transitions.append(c.reason)

    with patch("hare.query.core._run_stop_hooks_collect", new_callable=AsyncMock) as m:
        m.side_effect = fake_collect
        await drain_query(
            query(
                QueryParams(
                    messages=[],
                    system_prompt=["s"],
                    user_context={},
                    system_context={},
                    can_use_tool=allow_all_can_use_tool,
                    tool_use_context=make_tool_use_context(),
                    query_source="sdk",
                    max_turns=10,
                    deps=make_deps(call_model=call_model),
                    on_terminal=cap.on_terminal,
                    on_transition=on_tr,
                )
            )
        )
    assert "stop_hook_blocking" in transitions
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_terminal_hook_stopped_via_tool_updates_patch() -> None:
    """``hook_stopped`` is rare in the partial ``run_tool_use`` port; patch tool stream."""
    from hare.services.tools.tool_orchestration import MessageUpdate

    cap = TerminalCapture()
    ctx = make_tool_use_context(tools=[AbortDuringToolCall()])

    async def call_model(_p):
        yield assistant_with_tool_use(tool_id="toolu_hs_1", tool_name="abort_mid_tool")

    hook_att = create_attachment_message({"type": "hook_stopped_continuation"})

    async def fake_updates(*_a, **_k):
        yield MessageUpdate(message=hook_att, new_context=ctx)

    with patch("hare.query.core._run_tool_updates", side_effect=fake_updates):
        await drain_query(
            query(
                QueryParams(
                    messages=[],
                    system_prompt=["s"],
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
    assert cap.terminal.reason == "hook_stopped"


@pytest.mark.asyncio
async def test_terminal_aborted_tools_mid_execution() -> None:
    cap = TerminalCapture()
    ac = MutableAbortController()
    ctx = make_tool_use_context(tools=[AbortDuringToolCall()], abort_controller=ac)

    async def call_model(_p):
        yield assistant_with_tool_use(tool_id="toolu_ab_1", tool_name="abort_mid_tool")

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert cap.terminal.reason == "aborted_tools"


@pytest.mark.asyncio
async def test_transition_max_output_tokens_escalate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transitions: list[str] = []

    def on_tr(c):
        transitions.append(c.reason)

    monkeypatch.setattr(
        "hare.query.core.get_feature_value_cached_may_be_stale",
        lambda _k, _d: True,
    )

    n = {"inv": 0}

    async def call_model(_p):
        n["inv"] += 1
        if n["inv"] == 1:
            yield assistant_withheld_max_output_tokens()
        else:
            yield assistant_text_only("after escalate")

    cap = TerminalCapture()
    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
                on_transition=on_tr,
            )
        )
    )
    assert "max_output_tokens_escalate" in transitions
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_transition_max_output_tokens_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "hare.query.core.get_feature_value_cached_may_be_stale",
        lambda _k, _d: False,
    )
    transitions: list[str] = []

    def on_tr(c):
        transitions.append(c.reason)

    n = {"inv": 0}

    async def call_model(_p):
        n["inv"] += 1
        if n["inv"] == 1:
            yield assistant_withheld_max_output_tokens()
        else:
            yield assistant_text_only("after recovery")

    cap = TerminalCapture()
    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
                on_transition=on_tr,
            )
        )
    )
    assert "max_output_tokens_recovery" in transitions


@pytest.mark.asyncio
async def test_completed_after_max_output_recovery_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After ``MAX_OUTPUT_TOKENS_RECOVERY_LIMIT`` withhold retries, surface error → ``completed``."""
    monkeypatch.setattr(
        "hare.query.core.get_feature_value_cached_may_be_stale",
        lambda _k, _d: False,
    )
    cap = TerminalCapture()
    n = {"i": 0}

    async def call_model(_p):
        n["i"] += 1
        yield assistant_withheld_max_output_tokens()

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert cap.terminal.reason == "completed"
    assert n["i"] >= 4


@pytest.mark.asyncio
async def test_terminal_prompt_too_long_after_recovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "hare.query.core._try_collapse_drain_recovery", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "hare.query.core._try_reactive_recovery", AsyncMock(return_value=None)
    )

    cap = TerminalCapture()

    async def call_model(_p):
        yield assistant_withheld_prompt_too_long()

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert cap.terminal.reason == "prompt_too_long"


@pytest.mark.asyncio
async def test_terminal_image_error_after_media_recovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "hare.query.core._try_reactive_recovery", AsyncMock(return_value=None)
    )

    cap = TerminalCapture()

    async def call_model(_p):
        yield assistant_withheld_media_too_large()

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert cap.terminal.reason == "image_error"


@pytest.mark.asyncio
async def test_fallback_then_second_model_attempt() -> None:
    cap = TerminalCapture()
    n = {"i": 0}

    async def call_model(_p):
        n["i"] += 1
        if n["i"] == 1:
            raise FallbackTriggeredError()
        yield assistant_text_only("after fallback")

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                fallback_model="claude-haiku-4-20250514",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
            )
        )
    )
    assert n["i"] == 2
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_terminal_completed_on_plain_api_error_assistant() -> None:
    cap = TerminalCapture()

    async def call_model(_p):
        yield assistant_api_error_completed()

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_compact_query_source_skips_blocking_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``query_source in ('compact','session_memory')`` must not synthetic-block."""
    monkeypatch.setattr(
        "hare.query.core._is_at_blocking_limit",
        lambda _m, _ctx=None: True,
    )
    cap = TerminalCapture()

    async def call_model(_p):
        yield assistant_text_only("ok")

    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="compact",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
            )
        )
    )
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_autocompact_yields_post_compact_messages() -> None:
    summary = create_user_message(content="[summary]", is_meta=True)

    async def autocompact(*_a, **_k):
        return {
            "compactionResult": {
                "summaryMessages": [summary],
                "attachments": [],
                "hookResults": [],
            }
        }

    cap = TerminalCapture()

    async def cm(_p):
        yield assistant_text_only("post-compact reply")

    deps = make_deps(call_model=cm)
    deps.autocompact = AsyncMock(side_effect=autocompact)

    out = await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
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
    assert any(
        getattr(x, "type", None) == "user"
        and getattr(getattr(x, "message", None), "content", None)
        == summary.message.content
        for x in out
    )
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_token_budget_continuation_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_BUNDLE_FEATURE_TOKEN_BUDGET", "1")
    monkeypatch.setattr(
        "hare.query.core._is_at_blocking_limit",
        lambda _m, _ctx=None: False,
    )
    transitions: list[str] = []

    def on_tr(c):
        transitions.append(c.reason)

    from hare.query.token_budget import ContinueDecision, StopDecision

    tb_calls = [0]

    def fake_check(*_a, **_k):
        tb_calls[0] += 1
        if tb_calls[0] == 1:
            return ContinueDecision(
                nudge_message="[budget nudge]",
                continuation_count=1,
                pct=91,
                turn_tokens=910,
                budget=1000,
                action="continue",
            )
        return StopDecision(completion_event=None)

    monkeypatch.setattr("hare.query.core.check_token_budget", fake_check)
    monkeypatch.setattr(
        "hare.query.core._get_current_turn_token_budget",
        lambda: 1000,
    )
    monkeypatch.setattr("hare.query.core._get_turn_output_tokens", lambda: 910)

    phase = {"n": 0}

    async def call_model(_p):
        phase["n"] += 1
        if phase["n"] == 1:
            yield assistant_text_only("first")
        else:
            yield assistant_text_only("second")

    cap = TerminalCapture()

    async def empty_stop_hooks(**_k: Any) -> dict[str, Any]:
        return {"messages": [], "blocking_errors": [], "prevent_continuation": False}

    with patch("hare.query.core._run_stop_hooks_collect", new_callable=AsyncMock) as sm:
        sm.side_effect = empty_stop_hooks
        await drain_query(
            query(
                QueryParams(
                    messages=[],
                    system_prompt=["s"],
                    user_context={},
                    system_context={},
                    can_use_tool=allow_all_can_use_tool,
                    tool_use_context=make_tool_use_context(),
                    query_source="sdk",
                    deps=make_deps(call_model=call_model),
                    on_terminal=cap.on_terminal,
                    on_transition=on_tr,
                )
            )
        )
    assert "token_budget_continuation" in transitions
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_transition_collapse_drain_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    transitions: list[str] = []

    def on_tr(c):
        transitions.append(c.reason)

    from hare.query.core import _State

    async def fake_collapse(**kwargs: Any) -> Any:
        state: _State = kwargs["state"]
        mark_transition = kwargs["mark_transition"]
        tool_use_context = kwargs["tool_use_context"]
        nm = create_user_message(content="[drain]", is_meta=True)
        return replace(
            state,
            messages=[nm],
            tool_use_context=tool_use_context,
            transition=mark_transition(
                Continue(reason="collapse_drain_retry", committed=2)
            ),
        )

    monkeypatch.setattr(
        "hare.query.core._try_collapse_drain_recovery",
        AsyncMock(side_effect=fake_collapse),
    )
    monkeypatch.setattr(
        "hare.query.core._try_reactive_recovery",
        AsyncMock(return_value=None),
    )

    n = {"inv": 0}

    async def call_model(_p):
        n["inv"] += 1
        if n["inv"] == 1:
            yield assistant_withheld_prompt_too_long()
        else:
            yield assistant_text_only("after drain")

    cap = TerminalCapture()
    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
                on_transition=on_tr,
            )
        )
    )
    assert "collapse_drain_retry" in transitions
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"


@pytest.mark.asyncio
async def test_transition_reactive_compact_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transitions: list[str] = []

    def on_tr(c):
        transitions.append(c.reason)

    from hare.query.core import _State

    async def fake_reactive(**kwargs: Any) -> Any:
        state: _State = kwargs["state"]
        mark_transition = kwargs["mark_transition"]
        tool_use_context = kwargs["tool_use_context"]
        nm = create_user_message(content="[reactive]", is_meta=True)
        ns = replace(
            state,
            messages=[nm],
            tool_use_context=tool_use_context,
            has_attempted_reactive_compact=True,
            transition=mark_transition(Continue(reason="reactive_compact_retry")),
        )
        return (ns, kwargs.get("task_budget_remaining"), [nm])

    monkeypatch.setattr(
        "hare.query.core._try_collapse_drain_recovery",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "hare.query.core._try_reactive_recovery",
        AsyncMock(side_effect=fake_reactive),
    )

    n = {"inv": 0}

    async def call_model(_p):
        n["inv"] += 1
        if n["inv"] == 1:
            yield assistant_withheld_prompt_too_long()
        else:
            yield assistant_text_only("after reactive")

    cap = TerminalCapture()
    await drain_query(
        query(
            QueryParams(
                messages=[],
                system_prompt=["s"],
                user_context={},
                system_context={},
                can_use_tool=allow_all_can_use_tool,
                tool_use_context=make_tool_use_context(),
                query_source="sdk",
                deps=make_deps(call_model=call_model),
                on_terminal=cap.on_terminal,
                on_transition=on_tr,
            )
        )
    )
    assert "reactive_compact_retry" in transitions
    assert cap.terminal is not None
    assert cap.terminal.reason == "completed"
