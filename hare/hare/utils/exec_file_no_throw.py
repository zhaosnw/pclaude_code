"""
Async subprocess helpers that never raise (resolve to result dict).

Port of: src/utils/execFileNoThrow.ts
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

from hare.utils.cwd import get_cwd
from hare.utils.log import log_error

MS_IN_SECOND = 1000
SECONDS_IN_MINUTE = 60
_DEFAULT_TIMEOUT = 10 * SECONDS_IN_MINUTE * MS_IN_SECOND


def _error_message_from_result(
    short_message: str | None, signal: str | None, exit_code: int
) -> str:
    if short_message:
        return short_message
    if signal:
        return signal
    return str(exit_code)


async def exec_file_no_throw_with_cwd(
    file: str,
    args: list[str],
    *,
    abort_signal: Any | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    preserve_output_on_error: bool = True,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    max_buffer: int = 1_000_000,
    shell: bool | str | None = None,
    stdin: str | None = None,
    input: str | bytes | None = None,
) -> dict[str, Any]:
    """Run [file, *args]; always returns a dict (never raises)."""
    if abort_signal is not None and hasattr(abort_signal, "throw_if_aborted"):
        abort_signal.throw_if_aborted()

    timeout_sec = timeout / 1000.0
    env_use = env if env is not None else os.environ

    if stdin == "pipe":
        stdin_w = asyncio.subprocess.PIPE
    elif stdin == "ignore":
        stdin_w = subprocess.DEVNULL
    else:
        stdin_w = None  # inherit

    try:
        proc = await asyncio.create_subprocess_exec(
            file,
            *args,
            cwd=cwd,
            env=env_use,
            stdin=stdin_w,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        log_error(e)
        return {"stdout": "", "stderr": "", "code": 1, "error": str(e)}

    try:
        inp = input.encode() if isinstance(input, str) else input
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(inp), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        err = "timeout"
        if not preserve_output_on_error:
            return {"stdout": "", "stderr": "", "code": 1, "error": err}
        return {"stdout": "", "stderr": "", "code": 1, "error": err}
    except Exception as e:  # noqa: BLE001
        log_error(e if isinstance(e, Exception) else Exception(str(e)))
        return {"stdout": "", "stderr": "", "code": 1}

    code = proc.returncode if proc.returncode is not None else 1
    stdout = (out_b or b"").decode(errors="replace")[:max_buffer]
    stderr = (err_b or b"").decode(errors="replace")[:max_buffer]

    if code != 0:
        if preserve_output_on_error:
            return {
                "stdout": stdout,
                "stderr": stderr,
                "code": code,
                "error": _error_message_from_result(None, None, code),
            }
        return {"stdout": "", "stderr": "", "code": code}
    return {"stdout": stdout, "stderr": stderr, "code": 0}


async def exec_file_no_throw(
    file: str,
    args: list[str],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = options or {}
    timeout = opts.get("timeout", _DEFAULT_TIMEOUT)
    preserve = opts.get("preserve_output_on_error", True)
    use_cwd = opts.get("use_cwd", True)
    return await exec_file_no_throw_with_cwd(
        file,
        args,
        abort_signal=opts.get("abort_signal"),
        timeout=timeout,
        preserve_output_on_error=preserve,
        cwd=get_cwd() if use_cwd else None,
        env=opts.get("env"),
        stdin=opts.get("stdin"),
        input=opts.get("input"),
    )


from hare.utils.exec_file_no_throw_portable import exec_sync_with_defaults_deprecated

__all__ = [
    "exec_file_no_throw",
    "exec_file_no_throw_with_cwd",
    "exec_sync_with_defaults_deprecated",
]
