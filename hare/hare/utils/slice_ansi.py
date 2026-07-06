"""Slice strings that may contain ANSI escapes (port of sliceAnsi.ts)."""

from __future__ import annotations

import re

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def slice_ansi(s: str, start: int, end: int | None = None) -> str:
    """Best-effort slice by visible width; without ansi-tokenize, strip codes then slice."""
    stripped = _ANSI.sub("", s)
    if end is None:
        return stripped[start:]
    return stripped[start:end]
