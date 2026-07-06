"""
Bridge poll interval configuration.

Port of: src/bridge/pollConfig.ts + pollConfigDefaults.ts

Fetches poll config from GrowthBook with 5-min refresh window.
Schema-validated with defense-in-depth floors (100ms minimum).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PollIntervalConfig:
    """Bridge poll interval configuration with 8 fields matching TS."""

    # Poll interval when not at session capacity (ms)
    poll_interval_ms_not_at_capacity: int = 2000

    # Poll interval when at session capacity (ms). 0 = disabled (heartbeat-only)
    poll_interval_ms_at_capacity: int = 600_000  # 10 minutes

    # Non-exclusive heartbeat interval while at capacity (ms). 0 = disabled.
    # Runs alongside at-capacity polling, not instead of it.
    non_exclusive_heartbeat_interval_ms: int = 0

    # Multi-session variants
    multisession_poll_interval_ms_not_at_capacity: int = 2000
    multisession_poll_interval_ms_partial_capacity: int = 2000
    multisession_poll_interval_ms_at_capacity: int = 600_000  # 10 minutes

    # Minimum age of work items to reclaim (ms). Server requires ≥1.
    reclaim_older_than_ms: int = 5000

    # Session keepalive interval for v2 (ms). 0 = disabled.
    session_keepalive_interval_v2_ms: int = 120_000  # 2 minutes


DEFAULT_POLL_CONFIG = PollIntervalConfig()


def get_poll_interval_config(
    context: Optional[dict[str, Any]] = None,
) -> PollIntervalConfig:
    """Fetch bridge poll interval config from GrowthBook.

    Falls back to DEFAULT_POLL_CONFIG if the flag is absent or malformed.
    """
    ctx = context or {}
    getter = ctx.get("get_feature_value_cached_with_refresh")
    if getter:
        raw = getter(
            "tengu_bridge_poll_interval_config",
            DEFAULT_POLL_CONFIG,
            5 * 60 * 1000,  # 5-min TTL
        )
        return _parse_poll_config(raw)
    return DEFAULT_POLL_CONFIG


def _parse_poll_config(raw: Any) -> PollIntervalConfig:
    """Validate and parse raw GrowthBook poll config data."""
    if not isinstance(raw, dict):
        return DEFAULT_POLL_CONFIG

    try:
        cfg = PollIntervalConfig()

        if "poll_interval_ms_not_at_capacity" in raw:
            v = raw["poll_interval_ms_not_at_capacity"]
            if isinstance(v, (int, float)) and v >= 100:
                cfg.poll_interval_ms_not_at_capacity = int(v)

        if "poll_interval_ms_at_capacity" in raw:
            v = raw["poll_interval_ms_at_capacity"]
            if isinstance(v, (int, float)) and (v == 0 or v >= 100):
                cfg.poll_interval_ms_at_capacity = int(v)

        if "non_exclusive_heartbeat_interval_ms" in raw:
            v = raw["non_exclusive_heartbeat_interval_ms"]
            if isinstance(v, (int, float)) and v >= 0:
                cfg.non_exclusive_heartbeat_interval_ms = int(v)

        if "multisession_poll_interval_ms_not_at_capacity" in raw:
            v = raw["multisession_poll_interval_ms_not_at_capacity"]
            if isinstance(v, (int, float)) and v >= 100:
                cfg.multisession_poll_interval_ms_not_at_capacity = int(v)

        if "multisession_poll_interval_ms_partial_capacity" in raw:
            v = raw["multisession_poll_interval_ms_partial_capacity"]
            if isinstance(v, (int, float)) and v >= 100:
                cfg.multisession_poll_interval_ms_partial_capacity = int(v)

        if "multisession_poll_interval_ms_at_capacity" in raw:
            v = raw["multisession_poll_interval_ms_at_capacity"]
            if isinstance(v, (int, float)) and (v == 0 or v >= 100):
                cfg.multisession_poll_interval_ms_at_capacity = int(v)

        if "reclaim_older_than_ms" in raw:
            v = raw["reclaim_older_than_ms"]
            if isinstance(v, (int, float)) and v >= 1:
                cfg.reclaim_older_than_ms = int(v)

        if "session_keepalive_interval_v2_ms" in raw:
            v = raw["session_keepalive_interval_v2_ms"]
            if isinstance(v, (int, float)) and v >= 0:
                cfg.session_keepalive_interval_v2_ms = int(v)

        # Liveness check: at least one at-capacity mechanism must be enabled
        has_single_session_liveness = (
            cfg.non_exclusive_heartbeat_interval_ms > 0
            or cfg.poll_interval_ms_at_capacity > 0
        )
        if not has_single_session_liveness:
            return DEFAULT_POLL_CONFIG

        return cfg
    except Exception:
        return DEFAULT_POLL_CONFIG
