"""exec_hook must not orphan its subprocess when it times out.

Port-specific regression test — not a TS alignment test. `exec_hook` runs a
hook's shell command via `asyncio.create_subprocess_shell` and bounds the
wait with `asyncio.wait_for`. `asyncio.wait_for` timing out only stops
*awaiting* `proc.communicate()`; it does not touch the subprocess itself, so
a hook command that outlives its timeout used to keep running as an orphan
(leaking fds/CPU/children) after `exec_hook` had already returned an error.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from hare.utils.hooks.exec_hook import exec_hook


@pytest.fixture
def captured_procs(monkeypatch):
    """Capture every subprocess exec_hook spawns during the test.

    exec_hook.py does a plain ``import asyncio`` and calls
    ``asyncio.create_subprocess_shell`` directly, so patching the attribute
    on the shared ``asyncio`` module (imported here too) is visible to it —
    no need to reach into exec_hook's module namespace.
    """
    procs: list[asyncio.subprocess.Process] = []
    orig_create = asyncio.create_subprocess_shell

    async def _capture(*args, **kwargs):
        proc = await orig_create(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _capture)
    return procs


@pytest.mark.asyncio
async def test_timeout_kills_the_subprocess_instead_of_orphaning_it(captured_procs):
    """A command that outlives the timeout must actually be gone afterward.

    `sleep 5` under `create_subprocess_shell` execs directly (no fork) on a
    POSIX shell, so the shell's pid IS the sleeping process's pid — killing
    `proc` kills the sleep. We poll the pid with `os.kill(pid, 0)` (raises
    ProcessLookupError once the process is reaped) to prove it's gone, not
    just that exec_hook returned an error dict.
    """
    result = await exec_hook("sleep 5", timeout=0.1)

    assert result["success"] is False
    assert result["exit_code"] == 124
    assert len(captured_procs) == 1
    pid = captured_procs[0].pid

    # Give the kill signal a brief moment to land; poll instead of a fixed sleep.
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(f"subprocess pid={pid} is still alive after exec_hook timed out")


@pytest.mark.asyncio
async def test_timeout_reaps_the_process_no_zombie(captured_procs):
    """After a timeout, the Process object itself must report exited.

    This exercises the "actually await proc.wait()" half of the fix directly
    against exec_hook's own bookkeeping rather than OS pid probing:
    `returncode` is only populated once the process has actually been
    reaped, so a non-None value proves exec_hook awaited the kill rather
    than firing-and-forgetting it (which would leave a zombie).
    """
    result = await exec_hook("sleep 5", timeout=0.1)

    assert result["exit_code"] == 124
    assert len(captured_procs) == 1
    assert captured_procs[0].returncode is not None


@pytest.mark.asyncio
async def test_cancelling_the_caller_still_kills_the_subprocess(captured_procs):
    """Cancellation from further up the stack must clean up too, not just timeout.

    A caller can bound the whole exec_hook() call from outside (e.g. via
    ``asyncio.wait_for`` at a higher level — this is exactly how the
    print-mode exit path bounds SessionEnd hooks). That delivers
    ``CancelledError`` into exec_hook's own internal wait_for rather than a
    ``TimeoutError``, so the subprocess must still be cleaned up on that path
    too, and cancellation must still propagate (not be swallowed).
    """
    task = asyncio.ensure_future(exec_hook("sleep 5", timeout=600))
    await asyncio.sleep(0.2)
    assert len(captured_procs) == 1
    pid = captured_procs[0].pid

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(f"subprocess pid={pid} is still alive after the caller cancelled exec_hook")
