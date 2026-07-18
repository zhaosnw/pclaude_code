"""Port of: src/utils/hooks/execAgentHook.ts — shell command executor."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any


async def _kill_orphaned_process(proc: "asyncio.subprocess.Process") -> None:
    """Terminate and reap a subprocess whose ``communicate()`` was abandoned.

    ``asyncio.wait_for()`` timing out — or the task awaiting it being
    cancelled — only stops *awaiting* ``proc.communicate()``; it does not
    touch the subprocess itself. Left alone, that subprocess keeps running
    as an orphan, holding fds/CPU/children indefinitely. Killing without
    reaping would also leave a zombie, so we ``wait()`` after the kill.
    """
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


async def exec_hook(
    command: str,
    *,
    cwd: str = "",
    timeout: float = 30.0,
    input_data: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a hook shell command with optional JSON input on stdin.

    Args:
        command: Shell command string to execute
        cwd: Working directory for the subprocess
        timeout: Maximum execution time in seconds
        input_data: If provided, serialized as JSON and piped to stdin
        env: Additional environment variables to inject
    """
    try:
        # Build env: inherit current env + inject extra vars
        process_env = os.environ.copy()
        if env:
            for k, v in env.items():
                if v is not None:
                    process_env[k] = v

        # Prepare stdin
        stdin_pipe = asyncio.subprocess.PIPE if input_data is not None else None
        stdin_bytes: bytes | None = None
        if input_data is not None:
            stdin_bytes = (json.dumps(input_data, default=str) + "\n").encode("utf-8")

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd or None,
            env=process_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin_pipe,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await _kill_orphaned_process(proc)
            return {"success": False, "error": "Hook timed out", "exit_code": 124}
        except asyncio.CancelledError:
            # The task awaiting us was cancelled (e.g. a caller-side timeout
            # further up the stack, such as the print-mode exit path bounding
            # SessionEnd hooks). Clean up before letting cancellation
            # propagate — swallowing it here would break asyncio's
            # cancellation bookkeeping.
            await _kill_orphaned_process(proc)
            raise

        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
            "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            "exit_code": proc.returncode or 0,
        }
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return {"success": False, "error": str(e), "exit_code": 1}
