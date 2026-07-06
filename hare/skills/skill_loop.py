"""
Loop skill — run a prompt on a recurring interval.

Port of: src/skills/bundled/loop.ts (92 lines)

Parses interval descriptions like "every 5 minutes", converts to cron,
and sets up recurring execution via CronCreate.
"""

from __future__ import annotations

import re
from typing import Any


# Interval parsing — matches TS priority rules
_INTERVAL_UNITS: dict[str, int] = {
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "s": 1,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "m": 60,
    "hour": 3600,
    "hours": 3600,
    "hr": 3600,
    "h": 3600,
}
_CRON_FORMAT = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$")


def _parse_interval_to_cron(interval_str: str) -> str:
    """Parse a human interval to a cron expression.

    Priority: 1) existing cron expression, 2) "<N> <unit>", 3) "every <N> <unit>"
    """
    interval_str = interval_str.strip().lower()

    # 1. Already a valid cron expression
    parts = interval_str.split()
    if len(parts) == 5 and _CRON_FORMAT.match(interval_str):
        # Validate each part
        try:
            for p in parts:
                if p != "*" and "/" not in p:
                    int(p) if p.isdigit() else None
            return interval_str
        except (ValueError, TypeError):
            pass

    # 2. "<N> <unit>" pattern
    match = re.match(r"^(\d+)\s*(\w+)$", interval_str)
    if match:
        n = int(match.group(1))
        unit = match.group(2).lower()
        if unit in _INTERVAL_UNITS:
            seconds = n * _INTERVAL_UNITS[unit]
            return _seconds_to_cron(seconds)
        return _build_periodic_cron(n, unit)

    # 3. "every <N> <unit>" pattern
    match = re.match(r"^every\s+(\d+)\s*(\w+)$", interval_str)
    if match:
        n = int(match.group(1))
        unit = match.group(2).lower()
        if unit in _INTERVAL_UNITS:
            seconds = n * _INTERVAL_UNITS[unit]
            return _seconds_to_cron(seconds)
        return _build_periodic_cron(n, unit)

    raise ValueError(
        f"Could not parse interval: {interval_str}\n"
        "Usage: /loop [interval] <prompt>\n"
        "Examples: /loop 5m check the deploy, /loop 1h run smoke test"
    )


def _seconds_to_cron(seconds: int) -> str:
    """Convert seconds to a cron expression."""
    if seconds < 60:
        return f"*/{max(seconds, 1)} * * * *"
    if seconds < 3600:
        minutes = seconds // 60
        return f"*/{minutes} * * * *" if minutes > 0 else "* * * * *"
    if seconds < 86400:
        hours = seconds // 3600
        return f"0 */{hours} * * *" if hours > 0 else "0 * * * *"
    days = seconds // 86400
    return f"0 0 */{max(days, 1)} * *"


def _build_periodic_cron(n: int, unit: str) -> str:
    """Build a cron expression from a numeric interval."""
    if unit in ("min", "mins", "minute", "minutes"):
        return f"*/{n} * * * *" if n > 1 else "* * * * *"
    if unit in ("h", "hr", "hrs", "hour", "hours"):
        return f"0 */{n} * * *" if n > 1 else "0 * * * *"
    if unit in ("d", "day", "days"):
        return f"0 0 */{n} * *" if n > 1 else "0 0 * * *"
    return f"*/{n} * * * *"  # default: every N minutes


async def run_skill_loop(
    skill_name: str,
    max_iterations: int = 10,
    context: Any = None,
    cron_str: str | None = None,
) -> dict[str, Any]:
    """Set up a recurring loop for a skill or prompt.

    Returns the cron expression and scheduling info.
    """
    if not skill_name:
        raise ValueError("Usage: /loop <interval> <prompt>")

    # Parse interval from skill_name if it starts with a number
    interval_part = skill_name.split(None, 1)[0] if skill_name else ""
    remaining = (
        skill_name.split(None, 1)[1]
        if len(skill_name.split(None, 1)) > 1
        else skill_name
    )

    try:
        if interval_part and (interval_part.isdigit() or interval_part[0].isdigit()):
            cron_expr = _parse_interval_to_cron(interval_part)
            prompt = remaining
        elif cron_str:
            cron_expr = _parse_interval_to_cron(cron_str)
            prompt = skill_name
        else:
            # Default: every 10 minutes
            cron_expr = "*/10 * * * *"
            prompt = skill_name
    except ValueError:
        cron_expr = "*/10 * * * *"
        prompt = skill_name

    return {
        "status": "scheduled",
        "cron": cron_expr,
        "prompt": prompt,
        "max_iterations": max_iterations,
        "recurring": True,
    }
