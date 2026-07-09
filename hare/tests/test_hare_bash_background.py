"""Bash run_in_background + TaskOutput block/timeout (2.1.88 feature)."""

import asyncio
import re

import pytest

from hare.tools_impl.BashTool.bash_tool import BashTool
from hare.tools_impl.TaskTools.task_create_tool import get_task
from hare.tools_impl.TaskOutputTool import task_output_tool
from hare.tools_impl.TaskStopTool import task_stop_tool


def _task_id(data: str) -> str:
    m = re.search(r"task_id[:= ]+([0-9a-f]+)", data)
    assert m, f"no task_id in: {data!r}"
    return m.group(1)


def test_bash_schema_has_run_in_background():
    s = BashTool.input_schema()
    assert "run_in_background" in s["properties"]


def test_bash_run_in_background_then_taskoutput():
    async def go():
        res = await BashTool.call(
            {"command": "echo HELLO-BG", "run_in_background": True}, None
        )
        data = res.data if hasattr(res, "data") else str(res)
        assert "background" in data.lower()
        tid = _task_id(data)

        # blocking TaskOutput waits for completion and returns the output
        out = await task_output_tool.call(task_id=tid, block=True, timeout=15000)
        assert out.get("status") in ("completed", "success"), out
        assert "HELLO-BG" in (out.get("content") or ""), out

    asyncio.run(go())


def test_bash_run_in_background_returns_immediately():
    async def go():
        # a command that takes ~1s must not block the call
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        res = await BashTool.call(
            {"command": "sleep 1; echo SLOW", "run_in_background": True}, None
        )
        elapsed = loop.time() - t0
        assert elapsed < 0.8, f"background call blocked for {elapsed}s"
        tid = _task_id(res.data)
        assert get_task(tid) is not None
        # drain the task so the event loop tears down cleanly (no dangling proc)
        out = await task_output_tool.call(task_id=tid, block=True, timeout=10000)
        assert "SLOW" in (out.get("content") or "")
    asyncio.run(go())


def test_bash_background_taskstop():
    """TaskStop stops a background bash (via the TaskTools-registry fallback)."""
    async def go():
        res = await BashTool.call(
            {"command": "sleep 30; echo X", "run_in_background": True}, None
        )
        tid = _task_id(res.data)
        stop = await task_stop_tool.call(task_id=tid)
        assert stop.get("success"), stop
        # let the cancellation propagate (kills the subprocess)
        await asyncio.sleep(0.3)
        assert get_task(tid).status == "cancelled"
    asyncio.run(go())
