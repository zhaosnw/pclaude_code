"""Guard stdout for NDJSON stream-json mode (port of streamJsonStdoutGuard.ts)."""

from __future__ import annotations

import json
import sys
from typing import Callable, TextIO

from hare.utils.cleanup_registry import register_cleanup
from hare.utils.debug import log_for_debugging

STDOUT_GUARD_MARKER = "[stdout-guard]"

_installed = False
_buffer = ""
_original_write: Callable[[str], int] | None = None


def _is_json_line(line: str) -> bool:
    if not line:
        return True
    try:
        json.loads(line)
        return True
    except json.JSONDecodeError:
        return False


def install_stream_json_stdout_guard() -> None:
    global _installed, _original_write, _buffer
    if _installed:
        return
    _installed = True
    _buffer = ""
    out: TextIO = sys.stdout
    _original_write = out.write

    def guarded_write(s: str) -> int:
        global _buffer
        _buffer += s
        while True:
            nl = _buffer.find("\n")
            if nl == -1:
                break
            line = _buffer[:nl]
            _buffer = _buffer[nl + 1 :]
            assert _original_write is not None
            if _is_json_line(line):
                _original_write(line + "\n")
            else:
                sys.stderr.write(f"{STDOUT_GUARD_MARKER} {line}\n")
                log_for_debugging(
                    f"streamJsonStdoutGuard diverted non-JSON stdout line: {line[:200]}"
                )
        return len(s)

    out.write = guarded_write  # type: ignore[method-assign]

    async def _cleanup() -> None:
        global _installed, _buffer, _original_write
        if _buffer:
            if _original_write and _is_json_line(_buffer):
                _original_write(_buffer + "\n")
            else:
                sys.stderr.write(f"{STDOUT_GUARD_MARKER} {_buffer}\n")
            _buffer = ""
        if _original_write is not None:
            sys.stdout.write = _original_write
            _original_write = None
        _installed = False

    register_cleanup(_cleanup)


def _reset_stream_json_stdout_guard_for_testing() -> None:
    global _installed, _buffer, _original_write
    if _original_write is not None:
        sys.stdout.write = _original_write
        _original_write = None
    _buffer = ""
    _installed = False
