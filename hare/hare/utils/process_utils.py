"""
Process stdio helpers. Port of src/utils/process.ts.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, TextIO


def register_process_output_error_handlers() -> None:
    """Avoid leaks when stdout/stderr pipes break (e.g. `hare -p | head -1`)."""
    # Node registers EPIPE handlers; Python typically raises BrokenPipeError on write.


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
    print(message, file=sys.stderr)
    raise SystemExit(1)


async def peek_for_stdin_data(stream: Any, ms: int) -> bool:
    """
    True if `ms` passes with no byte read (timeout). False if a byte or EOF arrives first.
    Mirrors TS peekForStdinData (first chunk cancels timeout; EOF ends wait).
    """
    read = getattr(stream, "read", None)
    if not callable(read):
        return True
    try:
        await asyncio.wait_for(asyncio.to_thread(read, 1), timeout=ms / 1000.0)
    except asyncio.TimeoutError:
        return True
    return False
