"""
Resolve executables on PATH.

Port of: src/utils/findExecutable.ts
"""

from __future__ import annotations

from hare.utils.which import which_sync


def find_executable(exe: str, args: list[str]) -> dict[str, str | list[str]]:
    """Return `{cmd, args}` matching the historical spawn-rx shape."""
    resolved = which_sync(exe)
    return {"cmd": resolved or exe, "args": args}
