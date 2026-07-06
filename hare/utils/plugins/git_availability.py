"""
Memoized git availability check.

Port of: src/utils/plugins/gitAvailability.ts
"""

from __future__ import annotations

from hare.utils.which import which

_git_available: bool | None = None


async def check_git_available() -> bool:
    global _git_available
    if _git_available is not None:
        return _git_available
    try:
        _git_available = bool(await which("git"))
    except Exception:
        _git_available = False
    return _git_available


def mark_git_unavailable() -> None:
    global _git_available
    _git_available = False


def clear_git_availability_cache() -> None:
    global _git_available
    _git_available = None
