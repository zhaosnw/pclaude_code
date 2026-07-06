"""
Cron expression parsing and formatting.

Port of: src/utils/cron.ts

Supports standard 5-field cron expressions:
  minute hour day-of-month month day-of-week

Field syntax (per field):
  *         – any value
  N         – single value
  N-M       – inclusive range
  */N       – every N (same as 0-59/N for minute, etc.)
  M-N/N     – range with step
  A,B,C     – list (can combine with ranges/steps)

Named values:
  Month: JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC (case-insensitive)
  Day-of-week: SUN MON TUE WED THU FRI SAT (case-insensitive)
               Also numeric: 0 or 7 = Sunday

Standard cron semantics:
  When both dayOfMonth and dayOfWeek are constrained (neither is the full
  range), a date matches if EITHER matches (OR logic).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Public helpers (reexported for cron_tasks.py et al.)
# ---------------------------------------------------------------------------

__all__ = [
    "parse_cron_expression",
    "cron_to_human",
    "next_cron_run_ms",
    "validate_cron_expression",
    "expand_cron_field",
    "matches_cron",
    "compute_next_cron_run",
]


# =============================================================================
# Constants
# =============================================================================

# Lower / upper bounds for each field position (0-indexed)
_CRON_FIELDS = ("minute", "hour", "day_of_month", "month", "day_of_week")
_FIELD_RANGES: dict[str, tuple[int, int]] = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day_of_month": (1, 31),
    "month": (1, 12),
    "day_of_week": (0, 7),
}

# Month name -> number (3-letter, case-insensitive)
_MONTH_NAMES: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Day-of-week name -> number (3-letter, case-insensitive). 0 = Sunday.
_DOW_NAMES: dict[str, int] = {
    "SUN": 0, "MON": 1, "TUE": 2, "WED": 3,
    "THU": 4, "FRI": 5, "SAT": 6,
}

# Ordering for human-readable day-of-week.
_DOW_ORDER = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")

# Maps 0-based weekday numbers to full names (e.g., 0 → "Sunday").
_DOW_FULL_NAMES = [
    "Sunday", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday",
]

# Max iteration count for next-run search (366 days * 24h * 60m).
_MAX_ITERATIONS = 366 * 24 * 60

# Max days to scan ahead for impossible combinations like "30 Feb".
_MAX_SCAN_DAYS = 366 * 4


# =============================================================================
# Internal field parsing
# =============================================================================

def _parse_int(token: str, field_name: str) -> int | None:
    """Parse a single numeric token, returning None if not valid."""
    try:
        return int(token)
    except ValueError:
        return None


def _resolve_name(token: str, field_name: str) -> int | None:
    """Resolve a named month or day-of-week token.

    Returns the 1-based month number (1-12) or 0-based DoW (0-6),
    or None if the name is unrecognised.
    """
    upper = token.upper()
    if field_name == "month" and upper in _MONTH_NAMES:
        return _MONTH_NAMES[upper]
    if field_name == "day_of_week" and upper in _DOW_NAMES:
        return _DOW_NAMES[upper]
    return None


def _normalise_lo_hi(
    lo: int, hi: int, range_info: tuple[int, int]
) -> tuple[int, int] | None:
    """Clamp lo/hi into *range_info* and return (lo, hi) or None."""
    r_lo, r_hi = range_info
    lo = max(lo, r_lo)
    hi = min(hi, r_hi)
    if lo > hi:
        return None
    return lo, hi


def _parse_single_token(
    token: str, field_name: str, range_info: tuple[int, int]
) -> frozenset[int] | None:
    """Parse one token that may include a name, step, or range.

    Returns a frozenset of expanded values, or None on failure.
    """
    step: int | None = None

    # Split off step: ``*/5``, ``1-10/2``
    if "/" in token:
        token, step_str = token.split("/", 1)
        if not step_str:
            return None
        step = _parse_int(step_str, field_name)
        if step is None or step <= 0:
            return None

    # Wildcard
    if token == "*":
        lo, hi = range_info
    elif "-" in token:
        # Range: N-M
        parts = token.split("-", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None
        a = _resolve_name(parts[0], field_name)
        if a is None:
            a = _parse_int(parts[0], field_name)
        b = _resolve_name(parts[1], field_name)
        if b is None:
            b = _parse_int(parts[1], field_name)
        if a is None or b is None:
            return None
        lo, hi = a, b
    else:
        # Single value (numeric or name)
        val = _resolve_name(token, field_name)
        if val is None:
            val = _parse_int(token, field_name)
        if val is None:
            return None
        lo = hi = val

    # Normalise bounds
    bounds = _normalise_lo_hi(lo, hi, range_info)
    if bounds is None:
        return None
    lo, hi = bounds

    # Apply step
    if step is not None:
        values = frozenset(range(lo, hi + 1, step))
    else:
        values = frozenset(range(lo, hi + 1))

    # Special: day-of-week field allows both 0 and 7 = Sunday.
    # If 7 appears, also include 0 (and vice versa) for matching.
    if field_name == "day_of_week":
        extras: set[int] = set()
        if 0 in values:
            extras.add(7)
        if 7 in values:
            extras.add(0)
        if extras:
            values = values | frozenset(extras)

    return values


# =============================================================================
# Field expansion (public)
# =============================================================================

def expand_cron_field(field: str, field_name: str) -> frozenset[int] | None:
    """Parse *field* (e.g. ``1-5``, ``*/15``, ``MON,WED,FRI``) and return the set
    of matching values, or ``None`` if the field is invalid.

    This is a public helper useful for tests and tools that need to
    introspect a single cron field without parsing a full expression.
    """
    range_info = _FIELD_RANGES.get(field_name)
    if range_info is None:
        return None

    # Split on comma for list support
    tokens = field.split(",")
    all_values: set[int] = set()
    for token in tokens:
        token = token.strip()
        if not token:
            return None
        parsed = _parse_single_token(token, field_name, range_info)
        if parsed is None:
            return None
        all_values.update(parsed)

    if not all_values:
        return None
    return frozenset(all_values)


# =============================================================================
# Public API - parse / validate
# =============================================================================


def parse_cron_expression(expr: str) -> dict[str, Any] | None:
    """Parse a 5-field cron expression.

    Returns a dict with keys ``minute``, ``hour``, ``day_of_month``,
    ``month``, ``day_of_week`` — each the raw field string — or ``None``
    if the expression doesn't have exactly 5 fields *or* any field fails
    validation.
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return None

    for field_name, raw in zip(_CRON_FIELDS, parts):
        if expand_cron_field(raw, field_name) is None:
            return None

    return {
        "minute": parts[0],
        "hour": parts[1],
        "day_of_month": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def validate_cron_expression(expr: str) -> list[str]:
    """Validate a cron expression and return a list of error messages.

    Returns an empty list when the expression is valid.
    """
    errors: list[str] = []
    parts = expr.strip().split()
    if len(parts) != 5:
        errors.append(
            f"Expected 5 fields, got {len(parts)} (fields: {expr!r})"
        )
        return errors
    for field_name, raw in zip(_CRON_FIELDS, parts):
        result = expand_cron_field(raw, field_name)
        if result is None:
            errors.append(f"Invalid {field_name} field: {raw!r}")
        elif field_name == "day_of_month":
            # Warn if any values exceed 28 — they may not fire in February
            # but this is not an error (valid cron).
            pass
    return errors


# =============================================================================
# Human-readable descriptions
# =============================================================================


def _is_every_step(raw: str) -> str | None:
    """If *raw* is ``*/N`` return N, else None."""
    if raw.startswith("*/"):
        return raw[2:]
    return None


def _cron_field_name_to_human(field_name: str) -> str:
    """Return a human label for a field name."""
    return {
        "minute": "minute",
        "hour": "hour",
        "day_of_month": "day of month",
        "month": "month",
        "day_of_week": "day of week",
    }.get(field_name, field_name)


def cron_to_human(expr: str) -> str:
    """Convert a cron expression to a human-readable description.

    Returns the original expression when no specific pattern matches.
    """
    parsed = parse_cron_expression(expr)
    if not parsed:
        return expr

    m_raw, h_raw, dom_raw, mon_raw, dow_raw = (
        parsed["minute"],
        parsed["hour"],
        parsed["day_of_month"],
        parsed["month"],
        parsed["day_of_week"],
    )

    # ---- Every minute (all wildcards) -------------------------------------
    if m_raw == "*" and h_raw == "*" and dom_raw == "*" and mon_raw == "*" and dow_raw == "*":
        return "every minute"

    # ---- Every-N minute patterns ------------------------------------------
    step = _is_every_step(m_raw)
    if step is not None and h_raw == "*" and dom_raw == "*" and mon_raw == "*" and dow_raw == "*":
        n = int(step)
        if n == 1:
            return "every minute"
        return f"every {n} minutes"

    # ---- Every-N hour patterns (``0 */N * * *``) --------------------------
    step = _is_every_step(h_raw)
    if step is not None and m_raw == "0" and dom_raw == "*" and mon_raw == "*" and dow_raw == "*":
        n = int(step)
        if n == 1:
            return "every hour"
        return f"every {n} hours"

    # ---- Hourly -----------------------------------------------------------
    if m_raw == "0" and h_raw == "*" and dom_raw == "*" and mon_raw == "*" and dow_raw == "*":
        return "every hour"

    # ---- Every-N hours with specific minute --------------------------------
    step = _is_every_step(h_raw)
    if step is not None and _is_numeric(m_raw) and dom_raw == "*" and mon_raw == "*" and dow_raw == "*":
        n = int(step)
        m = int(m_raw)
        if n == 1:
            return f"every hour at :{str(m).zfill(2)}"
        return f"every {n} hours at :{str(m).zfill(2)}"

    # ---- Specific minute mark(s) every hour --------------------------------
    if h_raw == "*" and dom_raw == "*" and mon_raw == "*" and dow_raw == "*":
        m_vals = expand_cron_field(m_raw, "minute")
        if m_vals is not None and len(m_vals) <= 5:
            mins = ", ".join(f":{str(v).zfill(2)}" for v in sorted(m_vals))
            return f"every hour at {mins}"

    # ---- Midnight variants -------------------------------------------------
    if m_raw == "0" and h_raw == "0":
        if dom_raw == "*" and mon_raw == "*" and dow_raw == "*":
            return "at midnight every night"
        if dom_raw == "1" and mon_raw == "*" and dow_raw == "*":
            return "at midnight on the first day of each month"

    # ---- Daily at specific time -------------------------------------------
    if dom_raw == "*" and mon_raw == "*":
        h_vals = expand_cron_field(h_raw, "hour")
        m_vals = expand_cron_field(m_raw, "minute")
        if m_vals is not None and h_vals is not None:

            # Weekdays
            dow_vals = expand_cron_field(dow_raw, "day_of_week")
            if dow_vals is not None:
                pure_dow = {v for v in dow_vals if v != 7}  # normalise
                if pure_dow == {1, 2, 3, 4, 5}:
                    return _describe_time(m_vals, h_vals, "weekdays")
                if pure_dow == {0, 6}:
                    return _describe_time(m_vals, h_vals, "weekends")

            # Single day of week
            if dow_vals is not None and len(dow_vals - {7}) == 1:
                d = next(iter(dow_vals - {7}))
                name = _DOW_ORDER[d].capitalize()
                return _describe_time(m_vals, h_vals, f"every {name}")

            # Every day
            if dow_raw == "*":
                return _describe_time(m_vals, h_vals, "daily")

    # ---- Specific day of month (single) -----------------------------------
    if mon_raw == "*" and dow_raw == "*":
        dom_vals = expand_cron_field(dom_raw, "day_of_month")
        h_vals = expand_cron_field(h_raw, "hour")
        m_vals = expand_cron_field(m_raw, "minute")
        if (
            dom_vals is not None
            and len(dom_vals) == 1
            and h_vals is not None
            and m_vals is not None
        ):
            day = sorted(dom_vals)[0]
            return _describe_time(m_vals, h_vals, f"on day {day} of each month")

    # ---- Specific month(s) ------------------------------------------------
    if dom_raw == "*" and dow_raw == "*":
        mon_vals = expand_cron_field(mon_raw, "month")
        h_vals = expand_cron_field(h_raw, "hour")
        m_vals = expand_cron_field(m_raw, "minute")
        if mon_vals is not None and h_vals is not None and m_vals is not None:
            if len(mon_vals) == 1:
                mon = sorted(mon_vals)[0]
                name = calendar.month_abbr[mon]
                return _describe_time(m_vals, h_vals, f"daily in {name}")
            if len(mon_vals) <= 3:
                names = [calendar.month_abbr[m] for m in sorted(mon_vals)]
                return _describe_time(m_vals, h_vals, f"daily in {', '.join(names)}")

    # ---- Fallback: describe field-by-field --------------------------------
    return _describe_verbose(parsed)


def _is_numeric(raw: str) -> bool:
    """Return True if *raw* is a plain integer."""
    try:
        int(raw)
        return True
    except ValueError:
        return False


def _describe_time(
    m_vals: frozenset[int],
    h_vals: frozenset[int],
    prefix: str,
) -> str:
    """Format something like ``'daily at 09:00'`` or ``'daily at 9, 17'``."""
    sorted_h = sorted(h_vals)
    sorted_m = sorted(m_vals)

    # Single hour, single minute — e.g. "daily at 9:00"
    if len(sorted_m) == 1 and len(sorted_h) == 1:
        return f"{prefix} at {sorted_h[0]}:{str(sorted_m[0]).zfill(2)}"

    # Single hour, up to 3 minutes — e.g. "daily at 9:00, 9:30"
    if len(sorted_h) == 1:
        h = sorted_h[0]
        if len(sorted_m) <= 3:
            mins = ", ".join(f"{h}:{str(v).zfill(2)}" for v in sorted_m)
            return f"{prefix} at {mins}"
        return f"{prefix} at {h}"

    # Single minute, up to 3 hours — e.g. "daily at 9:00, 17:00"
    if len(sorted_m) == 1 and len(sorted_h) <= 3:
        m = str(sorted_m[0]).zfill(2)
        times = ", ".join(f"{h}:{m}" for h in sorted_h)
        return f"{prefix} at {times}"

    # Small number of both hours and minutes (<= 2 each = at most 4 combos)
    # — e.g. "daily at 9:00, 9:30, 17:00, 17:30"
    if len(sorted_h) <= 2 and len(sorted_m) <= 2:
        combos: list[str] = []
        for h in sorted_h:
            for m in sorted_m:
                combos.append(f"{h}:{str(m).zfill(2)}")
        return f"{prefix} at {', '.join(combos)}"

    # Cannot compactly describe — just return the prefix.
    return f"{prefix}"


def _describe_verbose(parsed: dict[str, Any]) -> str:
    """Fallback field-by-field description."""
    parts: list[str] = []
    for fn in _CRON_FIELDS:
        raw = parsed[fn]
        if raw == "*":
            continue
        label = _cron_field_name_to_human(fn)
        parts.append(f"{label} {raw}")
    if not parts:
        return "every minute"
    return ", ".join(parts)


# =============================================================================
# Next-run computation
# =============================================================================


def _parsed_fields(expr: str) -> tuple[dict[str, frozenset[int]], dict[str, str]] | None:
    """Return (expanded_values_by_field, raw_by_field) or None."""
    parsed = parse_cron_expression(expr)
    if not parsed:
        return None

    raw_by_field: dict[str, str] = {}
    expanded: dict[str, frozenset[int]] = {}
    for fn in _CRON_FIELDS:
        raw = parsed[fn]
        raw_by_field[fn] = raw
        vals = expand_cron_field(raw, fn)
        if vals is None:
            return None
        expanded[fn] = vals
    return expanded, raw_by_field


def _is_wildcard_field(expanded: frozenset[int], field_name: str) -> bool:
    """Return True if *expanded* represents the full range for *field_name*.

    Uses length-based heuristic which is more robust than exact set
    comparison (handles ``1-31`` vs ``*`` for day-of-month, etc.).
    """
    range_info = _FIELD_RANGES.get(field_name)
    if range_info is None:
        return False
    r_lo, r_hi = range_info

    if field_name == "day_of_month":
        # DOM range is 1-31 -> 31 values
        return len(expanded) == 31
    elif field_name == "day_of_week":
        # DoW range is 0-7, but 0 and 7 are both Sunday.
        # The expanded set could be {0,1,2,3,4,5,6} (7 values)
        # or {0,1,2,3,4,5,6,7} (8 values) — both mean "wildcard".
        return len(expanded) >= 7
    else:
        return len(expanded) == (r_hi - r_lo + 1)


def _is_day_match(
    dt: datetime,
    dom_vals: frozenset[int],
    dow_vals: frozenset[int],
    month_vals: frozenset[int],
) -> bool:
    """Return True if *dt* matches the day-of-month, day-of-week, and month
    constraints.

    Cron semantics: a day matches when EITHER day-of-month OR day-of-week
    is a wildcard.  If BOTH are non-wildcard the day matches when EITHER
    field matches (OR logic, not AND).
    """
    dom_is_wild = _is_wildcard_field(dom_vals, "day_of_month")
    dow_is_wild = _is_wildcard_field(dow_vals, "day_of_week")

    # Month check
    if dt.month not in month_vals:
        return False

    # When both are wildcarded any day is fine.
    if dom_is_wild and dow_is_wild:
        return True

    dom_ok = dt.day in dom_vals if not dom_is_wild else False
    # Python weekday(): Monday=0 ... Sunday=6.  Cron DoW: Sunday=0 ... Saturday=6.
    cron_dow = (dt.weekday() + 1) % 7
    dow_ok = cron_dow in dow_vals if not dow_is_wild else False

    # If only one is specified, that one must match.
    if not dom_is_wild and dow_is_wild:
        return dom_ok
    if dom_is_wild and not dow_is_wild:
        return dow_ok

    # Both specified: OR logic — either matching counts.
    return dom_ok or dow_ok


def next_cron_run_ms(expr: str, now_ms: float) -> float | None:
    """Calculate the timestamp (epoch ms) of the next cron fire.

    Returns ``None`` when *expr* is not a valid 5-field cron expression.

    DST: Uses UTC internally and iterates minute-by-minute.  Fixed-hour
    crons targeting a spring-forward gap hour skip that hour on the
    transition day.  Wildcard-hour crons fire at the first valid minute
    after the gap.
    """
    fields = _parsed_fields(expr)
    if fields is None:
        return None

    expanded, _raw = fields

    # Pass parsed fields to the core computation.
    return _compute_next_from_fields(expanded, now_ms)


def compute_next_cron_run(
    expanded: dict[str, frozenset[int]],
    from_dt: datetime,
) -> datetime | None:
    """Compute the next datetime strictly after *from_dt* that matches
    the already-parsed cron fields.

    This is a lower-level function for callers that have already parsed
    and expanded the fields.  It mirrors the TypeScript ``computeNextCronRun``.

    Parameters
    ----------
    expanded : dict
        Keys: minute, hour, day_of_month, month, day_of_week.
        Values are frozensets of matching integers.
    from_dt : datetime
        The reference datetime (timezone-aware or naive).  The returned
        datetime will have the same tzinfo as *from_dt*.

    Returns
    -------
    datetime | None
        The next matching datetime, or None if no match within the scan window.
    """
    # Preserve timezone info if present.
    tz = from_dt.tzinfo

    minute_vals = expanded["minute"]
    hour_vals = expanded["hour"]
    dom_vals = expanded["day_of_month"]
    month_vals = expanded["month"]
    dow_vals = expanded["day_of_week"]

    # Round up to the next whole minute (strictly after from_dt).
    candidate = from_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Safety cap to avoid infinite loops on impossible combinations
    # (e.g. "30 Feb" — 28 days will never match).
    deadline = candidate + timedelta(days=_MAX_SCAN_DAYS)

    iteration = 0
    while candidate <= deadline and iteration < _MAX_ITERATIONS:
        iteration += 1

        # Month filter — jump to next valid month if not in set.
        if candidate.month not in month_vals:
            candidate = _advance_to_next_valid_month(candidate, month_vals)
            continue

        # Day filter
        if not _is_day_match(candidate, dom_vals, dow_vals, month_vals):
            # Jump to start of next day.
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
            continue

        # Hour filter
        if candidate.hour not in hour_vals:
            candidate += timedelta(hours=1)
            candidate = candidate.replace(minute=0, second=0, microsecond=0)
            continue

        # Minute filter
        if candidate.minute not in minute_vals:
            candidate += timedelta(minutes=1)
            candidate = candidate.replace(second=0, microsecond=0)
            continue

        # All filters passed — restore timezone and return.
        if tz is not None:
            candidate = candidate.replace(tzinfo=tz)
        return candidate

    return None


def _compute_next_from_fields(
    expanded: dict[str, frozenset[int]],
    now_ms: float,
) -> float | None:
    """Inner helper: compute next run epoch-ms from already-parsed fields."""
    now_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    result = compute_next_cron_run(expanded, now_dt)
    if result is None:
        return None
    return result.timestamp() * 1000.0


def _advance_to_next_valid_month(
    candidate: datetime,
    month_vals: frozenset[int],
) -> datetime:
    """Move *candidate* to the 1st of the next month that is in *month_vals*."""
    sorted_months = sorted(month_vals)
    cur = candidate.month

    # Find the first month > cur
    for m in sorted_months:
        if m > cur:
            return candidate.replace(
                year=candidate.year,
                month=m,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )

    # Wrap to next year
    return candidate.replace(
        year=candidate.year + 1,
        month=sorted_months[0],
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


# =============================================================================
# Cron matching — check if a datetime matches a cron expression
# =============================================================================


def matches_cron(expr: str, dt: datetime) -> bool:
    """Check whether *dt* matches the 5-field cron expression *expr*.

    Returns False when *expr* is invalid or *dt* does not match.

    Examples
    --------
    >>> from datetime import datetime
    >>> matches_cron("0 9 * * 1", datetime(2026, 1, 5, 9, 0))  # Monday 9am
    True
    >>> matches_cron("0 9 * * 1", datetime(2026, 1, 6, 9, 0))  # Tuesday 9am
    False
    """
    parsed = parse_cron_expression(expr)
    if not parsed:
        return False

    # Expand all fields
    expanded: dict[str, frozenset[int]] = {}
    for fn in _CRON_FIELDS:
        vals = expand_cron_field(parsed[fn], fn)
        if vals is None:
            return False
        expanded[fn] = vals

    # Check minute, hour, month
    if dt.minute not in expanded["minute"]:
        return False
    if dt.hour not in expanded["hour"]:
        return False
    if dt.month not in expanded["month"]:
        return False

    # Day check (OR semantics for DOM vs DOW).
    dom_is_wild = _is_wildcard_field(expanded["day_of_month"], "day_of_month")
    dow_is_wild = _is_wildcard_field(expanded["day_of_week"], "day_of_week")

    if dom_is_wild and dow_is_wild:
        return True

    dom_ok = dt.day in expanded["day_of_month"] if not dom_is_wild else False
    cron_dow = (dt.weekday() + 1) % 7
    dow_ok = cron_dow in expanded["day_of_week"] if not dow_is_wild else False

    if not dom_is_wild and dow_is_wild:
        return dom_ok
    if dom_is_wild and not dow_is_wild:
        return dow_ok

    return dom_ok or dow_ok
