"""
Locale-aware brief timestamps for chat labels.

Port of: src/utils/formatBriefTimestamp.ts
"""

from __future__ import annotations

import os
from datetime import datetime


def _start_of_day(d: datetime) -> float:
    return datetime(d.year, d.month, d.day).timestamp()


def format_brief_timestamp(iso_string: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    try:
        d = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if d.tzinfo:
        d = d.astimezone().replace(tzinfo=None)
    now_n = now.replace(tzinfo=None) if now.tzinfo else now
    day_diff = _start_of_day(now_n) - _start_of_day(d)
    days_ago = round(day_diff / 86400)
    # POSIX LC_* affects locale; Python `strftime` uses C locale unless setlocale called.
    raw = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_TIME")
        or os.environ.get("LANG")
        or ""
    )
    use_24h = any(x in raw.upper() for x in ("UTF-8", "EU", "DE", "FR")) or False
    if days_ago == 0:
        return d.strftime("%H:%M") if use_24h else d.strftime("%I:%M %p").lstrip("0")
    if 0 < days_ago < 7:
        return (
            d.strftime("%A %H:%M")
            if use_24h
            else f"{d.strftime('%A')} {d.strftime('%I:%M %p').lstrip('0')}"
        )
    return (
        d.strftime("%a %b %d %H:%M")
        if use_24h
        else f"{d.strftime('%a %b %d')} {d.strftime('%I:%M %p').lstrip('0')}"
    )
