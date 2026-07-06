"""
Early stdin capture before the REPL is ready.

Port of: src/utils/earlyInput.ts
"""

from __future__ import annotations

import sys
import unicodedata
from typing import Callable


def _last_grapheme_cluster(text: str) -> str:
    """Last user-perceived character; combining marks attach to previous base."""
    if not text:
        return ""
    i = len(text) - 1
    while i > 0 and unicodedata.combining(text[i]):
        i -= 1
    return text[i:]


_early_buffer = ""
_capturing = False
_readable_handler: Callable[[], None] | None = None


def start_capturing_early_input() -> None:
    global _capturing
    argv = sys.argv[1:]
    if not sys.stdin.isatty() or _capturing or "-p" in argv or "--print" in argv:
        return
    # Full raw-mode capture is platform-specific; Ink-equivalent integration is stubbed.
    _capturing = True
    # Caller may replace with tty/termios integration on POSIX.


def stop_capturing_early_input() -> None:
    global _capturing, _readable_handler
    _capturing = False
    _readable_handler = None


def consume_early_input() -> str:
    global _early_buffer
    stop_capturing_early_input()
    s = _early_buffer.strip()
    _early_buffer = ""
    return s


def has_early_input() -> bool:
    return len(_early_buffer.strip()) > 0


def seed_early_input(text: str) -> None:
    global _early_buffer
    _early_buffer = text


def is_capturing_early_input() -> bool:
    return _capturing


def _process_chunk(s: str) -> None:
    global _early_buffer
    i = 0
    while i < len(s):
        char = s[i]
        code = ord(char)
        if code == 3:
            stop_capturing_early_input()
            raise SystemExit(130)
        if code == 4:
            stop_capturing_early_input()
            return
        if code in (127, 8):
            if _early_buffer:
                last = _last_grapheme_cluster(_early_buffer)
                _early_buffer = _early_buffer[: -len(last) or -1]
            i += 1
            continue
        if code == 27:
            i += 1
            while i < len(s) and not (64 <= ord(s[i]) <= 126):
                i += 1
            if i < len(s):
                i += 1
            continue
        if code < 32 and code not in (9, 10, 13):
            i += 1
            continue
        if code == 13:
            _early_buffer += "\n"
            i += 1
            continue
        _early_buffer += char
        i += 1
