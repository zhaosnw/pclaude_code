"""
Ripgrep (`rg`) invocation helpers. Port of src/utils/ripgrep.ts (simplified).
"""

from __future__ import annotations

import asyncio
import shutil
from functools import lru_cache
from typing import Any, Callable

from hare.utils.exec_file_no_throw import exec_file_no_throw
from hare.utils.log import log_error

MAX_BUFFER_SIZE = 20_000_000


class RipgrepTimeoutError(Exception):
    def __init__(self, message: str, partial_results: list[str]) -> None:
        super().__init__(message)
        self.partial_results = partial_results


@lru_cache(maxsize=1)
def _ripgrep_command() -> tuple[str, list[str], str | None]:
    if shutil.which("rg"):
        return ("rg", [], None)
    return ("rg", [], None)


def ripgrep_command() -> dict[str, Any]:
    path, args, argv0 = _ripgrep_command()
    return {"rgPath": path, "rgArgs": args, "argv0": argv0}


def _is_eagain(stderr: str) -> bool:
    return "os error 11" in stderr or "Resource temporarily unavailable" in stderr


async def rip_grep(
    args: list[str], target: str, abort_signal: Any | None = None
) -> list[str]:
    rg_path, rg_args, _ = _ripgrep_command()
    full_args = rg_args + args + [target]
    if abort_signal is not None and hasattr(abort_signal, "throw_if_aborted"):
        abort_signal.throw_if_aborted()
    r = await exec_file_no_throw(
        rg_path,
        full_args,
        {"timeout": 20_000, "use_cwd": False},
    )
    code = r.get("code", 1)
    stdout = r.get("stdout") or ""
    stderr = r.get("stderr") or ""
    if code == 0:
        return [ln.rstrip("\r") for ln in stdout.strip().split("\n") if ln.strip()]
    if code == 1:
        return []
    if not _is_eagain(stderr):
        log_error(RuntimeError(stderr or f"rg exit {code}"))
    lines = [ln.rstrip("\r") for ln in stdout.strip().split("\n") if ln.strip()]
    return lines


async def rip_grep_stream(
    args: list[str],
    target: str,
    abort_signal: Any,
    on_lines: Callable[[list[str]], None],
) -> None:
    """Stream lines (simplified: buffers full stdout)."""
    lines = await rip_grep(args, target, abort_signal)
    if lines:
        on_lines(lines)


async def count_files_rounded_rg(
    dir_path: str,
    abort_signal: Any,
    ignore_patterns: list[str] | None = None,
) -> int | None:
    """Approximate file count via `rg --files` (rounded to nearest power of 10)."""
    import math
    from pathlib import Path

    home = Path.home().resolve()
    if Path(dir_path).resolve() == home:
        return None
    args = ["--files", "--hidden"]
    for p in ignore_patterns or []:
        args.extend(["--glob", f"!{p}"])
    try:
        proc = await asyncio.create_subprocess_exec(
            "rg",
            *args,
            dir_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout
        n = 0
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                break
            n += chunk.count(b"\n")
        await proc.wait()
        if n == 0:
            return 0
        mag = int(math.floor(math.log10(n)))
        p10 = 10**mag
        return round(n / p10) * p10
    except Exception as e:
        if getattr(e, "__class__", type).__name__ != "AbortError":
            log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return None


def get_ripgrep_status() -> dict[str, Any]:
    path, _, _ = _ripgrep_command()
    return {"mode": "system", "path": path, "working": shutil.which("rg") is not None}
