"""Memory age helpers (port of src/memdir/memoryAge.ts)."""

from __future__ import annotations

_MS_PER_DAY = 86_400_000


def memory_age_days(mtime_ms: float) -> int:
    import time

    return max(0, int((time.time() * 1000 - mtime_ms) / _MS_PER_DAY))


def memory_age(mtime_ms: float) -> str:
    d = memory_age_days(mtime_ms)
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    return f"{d} days ago"


def memory_freshness_text(mtime_ms: float) -> str:
    d = memory_age_days(mtime_ms)
    if d <= 1:
        return ""
    return (
        f"This memory is {d} days old. "
        "Memories are point-in-time observations, not live state — "
        "claims about code behavior or file:line citations may be outdated. "
        "Verify against current code before asserting as fact."
    )


def memory_freshness_note(mtime_ms: float) -> str:
    text = memory_freshness_text(mtime_ms)
    if not text:
        return ""
    return f"<system-reminder>{text}</system-reminder>\n"
