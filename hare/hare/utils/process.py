"""
Process stdout/stderr helpers and stdin peek. Port of: src/utils/process.ts
"""

from __future__ import annotations

import asyncio
import select
import sys
from typing import Any, TextIO


def register_process_output_error_handlers() -> None:
    """Best-effort: avoid leaks when stdout/stderr pipes break (e.g. `| head`)."""
    # Node attaches 'error' handlers; Python file objects differ — intentional no-op.
    _ = sys.stdout, sys.stderr


def _write_out(stream: TextIO, data: str) -> None:
    if getattr(stream, "closed", False):
        return
    try:
        stream.write(data)
    except BrokenPipeError:
        pass


def write_to_stdout(data: str) -> None:
    _write_out(sys.stdout, data)


def write_to_stderr(data: str) -> None:
    _write_out(sys.stderr, data)


def exit_with_error(message: str) -> None:
    write_to_stderr(message + "\n")
    sys.exit(1)


async def peek_for_stdin_data(stream: Any, ms: int) -> bool:
    """
    Returns True if the peek window expires with no activity (idle pipe).
    Mirrors Node: race timeout vs first 'data' / 'end'.

    On POSIX, uses select; if the fd becomes readable, treats as non-timeout
    (may be EOF — caller still reads full stdin afterward).
    """
    if not hasattr(stream, "fileno"):
        return False
    try:
        fd = stream.fileno()
    except Exception:
        return False
    if fd < 0:
        return False

    loop = asyncio.get_running_loop()

    def _select() -> bool:
        r, _, _ = select.select([fd], [], [], ms / 1000.0)
        return len(r) == 0

    return await loop.run_in_executor(None, _select)
