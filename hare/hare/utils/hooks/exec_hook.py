"""Port of: src/utils/hooks/execAgentHook.ts — shell command executor."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any


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

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout,
        )

        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
            "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            "exit_code": proc.returncode or 0,
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": "Hook timed out", "exit_code": 124}
    except Exception as e:
        return {"success": False, "error": str(e), "exit_code": 1}
