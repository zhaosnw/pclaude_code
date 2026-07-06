"""GrowthBook-backed cron jitter configuration (`cronJitterConfig.ts`).

Separated from cron_scheduler.py so the SDK can be bundled without pulling
in analytics/growthbook.py.  Falls back to DEFAULT_CRON_JITTER_CONFIG when
GrowthBook is unavailable or the feature flag is unset/malformed.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Optional, TypedDict

from hare.utils.cron_tasks import DEFAULT_CRON_JITTER_CONFIG

# Tight refresh for incident-response; hard upper bounds for safety.
JITTER_CONFIG_REFRESH_MS = 60 * 1000              # re-fetch every 1 min
HALF_HOUR_MS = 30 * 60 * 1000                     # max one-shot / recur cap
THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000          # max recurring task age


class CronJitterConfig(TypedDict, total=False):
    """Validated cron jitter config — mirrors cronJitterConfig.ts Zod schema."""
    recurringFrac: float       # [0, 1]
    recurringCapMs: int        # [0, HALF_HOUR_MS]
    oneShotMaxMs: int          # [0, HALF_HOUR_MS]
    oneShotFloorMs: int        # [0, HALF_HOUR_MS], must be <= oneShotMaxMs
    oneShotMinuteMod: int      # [1, 60]
    recurringMaxAgeMs: int     # [0, THIRTY_DAYS_MS]


# -- Validation utilities (mirrors cronJitterConfig.ts Zod schema) --

def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)
def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)
def _int_in(v: Any, lo: int, hi: int) -> bool:
    return _is_int(v) and lo <= v <= hi
_SENTINEL = object()


def validate_cron_jitter_config(raw: Any) -> Optional[CronJitterConfig]:
    """Validate raw dict.  Returns CronJitterConfig or None on any violation.

    Callers fall back to defaults wholesale — defense-in-depth.
    """
    if not isinstance(raw, dict):
        return None
    rf = raw.get("recurringFrac")
    if not _is_number(rf) or not (0.0 <= float(rf) <= 1.0):
        return None
    rc = raw.get("recurringCapMs")
    if not _int_in(rc, 0, HALF_HOUR_MS):
        return None
    osm = raw.get("oneShotMaxMs")
    if not _int_in(osm, 0, HALF_HOUR_MS):
        return None
    osf = raw.get("oneShotFloorMs")
    if not _int_in(osf, 0, HALF_HOUR_MS) or int(osf) > int(osm):
        return None
    omm = raw.get("oneShotMinuteMod")
    if not _int_in(omm, 1, 60):
        return None
    rma = raw.get("recurringMaxAgeMs", _SENTINEL)
    if rma is _SENTINEL:
        rma = DEFAULT_CRON_JITTER_CONFIG["recurringMaxAgeMs"]
    elif not _int_in(rma, 0, THIRTY_DAYS_MS):
        return None
    return CronJitterConfig(
        recurringFrac=float(rf),
        recurringCapMs=int(rc),
        oneShotMaxMs=int(osm),
        oneShotFloorMs=int(osf),
        oneShotMinuteMod=int(omm),
        recurringMaxAgeMs=int(rma),
    )


# -- GrowthBook integration --

def _fetch_raw_jitter_config() -> Any:
    """Fetch 'tengu_kairos_cron_config' from GrowthBook; fall back to defaults."""
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_CACHED_WITH_REFRESH,
        )
        return get_feature_value_CACHED_WITH_REFRESH(
            "tengu_kairos_cron_config", DEFAULT_CRON_JITTER_CONFIG
        )
    except ImportError:
        return DEFAULT_CRON_JITTER_CONFIG


# -- Cached config access (main public API) --

_cached_config: Optional[tuple[float, CronJitterConfig]] = None


def _default_config() -> CronJitterConfig:
    d = DEFAULT_CRON_JITTER_CONFIG
    return CronJitterConfig(
        recurringFrac=float(d["recurringFrac"]),
        recurringCapMs=int(d["recurringCapMs"]),
        oneShotMaxMs=int(d["oneShotMaxMs"]),
        oneShotFloorMs=int(d["oneShotFloorMs"]),
        oneShotMinuteMod=int(d["oneShotMinuteMod"]),
        recurringMaxAgeMs=int(d["recurringMaxAgeMs"]),
    )


def get_cron_jitter_config(
    _fetcher: Optional[Callable[[], Any]] = None,
) -> CronJitterConfig:
    """Return validated jitter config, cached for JITTER_CONFIG_REFRESH_MS.

    Called from the scheduler every tick — cheap sync cache hit after the
    first call.  ``_fetcher`` allows test injection.
    """
    global _cached_config
    now_ms = time.time() * 1000.0
    if _cached_config is not None:
        cached_at, cached_val = _cached_config
        if now_ms - cached_at < JITTER_CONFIG_REFRESH_MS:
            return cached_val
    raw = _fetcher() if _fetcher is not None else _fetch_raw_jitter_config()
    validated = validate_cron_jitter_config(raw)
    if validated is None:
        validated = _default_config()
    _cached_config = (now_ms, validated)
    return validated


def reset_cron_jitter_cache() -> None:
    """Clear in-memory cache; next call re-fetches and re-validates."""
    global _cached_config
    _cached_config = None


# -- Jitter calculation — deterministic per task, consumed by the scheduler --

def jitter_frac(task_id: str) -> float:
    """Deterministic [0,1) fraction from first 8 hex chars of task_id (port of cronTasks.ts)."""
    try:
        return int(task_id[:8], 16) / float(0x100000000)
    except (ValueError, IndexError):
        return 0.0


def compute_recurring_jitter_ms(interval_ms: float, task_id: str,
                             cfg: Optional[CronJitterConfig] = None) -> float:
    """Jitter for a recurring task: fraction of interval, capped at recurringCapMs."""
    if cfg is None:
        cfg = get_cron_jitter_config()
    return min(jitter_frac(task_id) * float(cfg["recurringFrac"]) * interval_ms,
               float(cfg["recurringCapMs"]))

def compute_one_shot_jitter_ms(task_id: str,
                                cfg: Optional[CronJitterConfig] = None) -> float:
    """Early-fire lead for one-shot tasks in [oneShotFloorMs, oneShotMaxMs)."""
    if cfg is None:
        cfg = get_cron_jitter_config()
    floor = float(cfg["oneShotFloorMs"])
    ceil = float(cfg["oneShotMaxMs"])
    return floor + jitter_frac(task_id) * (ceil - floor)

def compute_one_shot_jittered_fire_time_ms(base_fire_time_ms: float, task_id: str,
                                            cfg: Optional[CronJitterConfig] = None) -> float:
    """Move one-shot fire time earlier by a deterministic lead.

    Only applies when the base minute is a multiple of oneShotMinuteMod
    (e.g. :00 and :30).  Returns timestamp never later than base, never
    earlier than now.
    """
    if cfg is None:
        cfg = get_cron_jitter_config()
    dt = datetime.fromtimestamp(base_fire_time_ms / 1000.0)
    if dt.minute % int(cfg["oneShotMinuteMod"]) != 0:
        return base_fire_time_ms
    lead_ms = compute_one_shot_jitter_ms(task_id, cfg)
    return max(base_fire_time_ms - lead_ms, time.time() * 1000.0)
