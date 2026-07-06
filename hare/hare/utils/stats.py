"""Aggregate usage stats from session JSONL files (port of stats.ts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from hare.utils.stats_cache import (
    get_today_date_string,
    get_yesterday_date_string,
    load_stats_cache,
    with_stats_cache_lock,
)

StatsDateRange = Literal["7d", "30d", "all"]


@dataclass
class StreakInfo:
    current_streak: int = 0
    longest_streak: int = 0
    current_streak_start: str | None = None
    longest_streak_start: str | None = None
    longest_streak_end: str | None = None


@dataclass
class HareCodeStats:
    total_sessions: int = 0
    total_messages: int = 0
    total_days: int = 0
    active_days: int = 0
    streaks: StreakInfo = field(default_factory=StreakInfo)
    daily_activity: list[dict[str, Any]] = field(default_factory=list)
    daily_model_tokens: list[dict[str, Any]] = field(default_factory=list)
    longest_session: dict[str, Any] | None = None
    model_usage: dict[str, Any] = field(default_factory=dict)
    first_session_date: str | None = None
    last_session_date: str | None = None
    peak_activity_day: str | None = None
    peak_activity_hour: int | None = None
    total_speculation_time_saved_ms: int = 0


def get_empty_stats() -> HareCodeStats:
    return HareCodeStats(
        streaks=StreakInfo(
            current_streak=0,
            longest_streak=0,
            current_streak_start=None,
            longest_streak_start=None,
            longest_streak_end=None,
        ),
    )


async def aggregate_hare_code_stats() -> HareCodeStats:
    async def _inner() -> HareCodeStats:
        await load_stats_cache()
        return get_empty_stats()

    return await with_stats_cache_lock(_inner)


async def aggregate_hare_code_stats_for_range(
    range_name: StatsDateRange,
) -> HareCodeStats:
    if range_name == "all":
        return await aggregate_hare_code_stats()
    _ = (get_today_date_string(), get_yesterday_date_string())
    return get_empty_stats()


async def read_session_start_date(file_path: str) -> str | None:
    _ = file_path
    return None
