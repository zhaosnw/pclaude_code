"""execute_session_end_hooks must not block process exit on a hanging hook.

Port-specific regression test — not a TS alignment test. `main.py`'s
print-mode `finally` block calls `execute_session_end_hooks(reason="exit")`
on every exit from `-p` mode. `_run_simple_event_hooks` runs all matching
hooks concurrently via `asyncio.gather` but the whole call still awaits
until every hook finishes (or hits its own TOOL_HOOK_EXECUTION_TIMEOUT_MS —
10 minutes), so a single hanging/misconfigured SessionEnd hook used to hold
up every print-mode invocation's exit for up to 10 minutes.

These tests register a real hook callback through the same
`AsyncHookRegistry.register()` path exercised elsewhere (see
tests/test_tool_hooks_runtime.py, tests/e2e/test_subagent_async_reentry.py)
rather than spinning up a full main.py print-mode integration test.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from hare.utils.hooks import execute_session_end_hooks, get_hook_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    registry = get_hook_registry()
    registry.clear()
    yield
    registry.clear()


@pytest.mark.asyncio
async def test_exit_path_timeout_bounds_a_hanging_session_end_hook():
    """A SessionEnd hook that never returns must not hang the caller forever.

    Without a `timeout_sec` bound, this hook would hold `await
    execute_session_end_hooks(...)` open indefinitely (it never resolves on
    its own). With the exit-path bound, the call must return within a small
    multiple of the bound instead of hanging.
    """
    registry = get_hook_registry()

    async def hangs_forever(context: dict) -> dict:
        await asyncio.sleep(3600)
        return {}

    registry.register("SessionEnd", "test-hang", hangs_forever, source="test")

    start = time.monotonic()
    results = await asyncio.wait_for(
        execute_session_end_hooks(reason="exit", timeout_sec=0.2),
        timeout=5.0,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"execute_session_end_hooks took {elapsed}s, expected ~0.2s bound"
    # The hook never finished within the bound, so it contributes no result —
    # it's abandoned/cancelled, not awaited to completion.
    assert results == []


@pytest.mark.asyncio
async def test_exit_path_timeout_does_not_change_behavior_for_fast_hooks():
    """A hook that finishes quickly must still run to completion and report.

    This is the overwhelmingly common case; the exit-path bound must be a
    no-op for it — same results as calling with no timeout at all.
    """
    registry = get_hook_registry()
    calls: list[str] = []

    async def fast_hook(context: dict) -> dict:
        calls.append(context.get("reason", ""))
        return {"ok": True}

    registry.register("SessionEnd", "test-fast", fast_hook, source="test")

    results = await asyncio.wait_for(
        execute_session_end_hooks(reason="exit", timeout_sec=5.0),
        timeout=5.0,
    )

    assert calls == ["exit"]
    assert results == [{"ok": True}]


@pytest.mark.asyncio
async def test_no_timeout_arg_preserves_prior_unbounded_behavior():
    """Omitting timeout_sec must behave exactly as before this fix.

    Other callers of execute_session_end_hooks (e.g. /clear) don't pass
    timeout_sec at all, so the default must keep running hooks to completion
    (or their own per-hook timeout), not silently adopt a short bound.
    """
    registry = get_hook_registry()

    async def slow_but_finite(context: dict) -> dict:
        await asyncio.sleep(0.3)
        return {"done": True}

    registry.register("SessionEnd", "test-slow", slow_but_finite, source="test")

    results = await asyncio.wait_for(
        execute_session_end_hooks(reason="exit"),
        timeout=5.0,
    )

    assert results == [{"done": True}]
