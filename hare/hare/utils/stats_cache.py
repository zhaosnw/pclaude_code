"""Disk-backed stats cache (port of statsCache.ts)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.errors import error_message
from hare.utils.fs_operations import get_fs_implementation
from hare.utils.log import log_error
from hare.utils.slow_operations import json_parse, json_stringify

STATS_CACHE_VERSION = 3
MIN_MIGRATABLE_VERSION = 1
STATS_CACHE_FILENAME = "stats-cache.json"

_stats_lock: Any = None


def _get_stats_lock() -> Any:
    global _stats_lock
    if _stats_lock is None:
        import asyncio

        _stats_lock = asyncio.Lock()
    return _stats_lock


async def with_stats_cache_lock(fn: Any) -> Any:
    async with _get_stats_lock():
        return await fn()


@dataclass
class DailyActivity:
    date: str
    message_count: int
    session_count: int
    tool_call_count: int


@dataclass
class DailyModelTokens:
    date: str
    tokens_by_model: dict[str, int]


@dataclass
class SessionStats:
    session_id: str
    duration: int
    message_count: int
    timestamp: str


@dataclass
class PersistedStatsCache:
    version: int = STATS_CACHE_VERSION
    last_computed_date: str | None = None
    daily_activity: list[DailyActivity] = field(default_factory=list)
    daily_model_tokens: list[DailyModelTokens] = field(default_factory=list)
    model_usage: dict[str, Any] = field(default_factory=dict)
    total_sessions: int = 0
    total_messages: int = 0
    longest_session: SessionStats | None = None
    first_session_date: str | None = None
    hour_counts: dict[int, int] = field(default_factory=dict)
    total_speculation_time_saved_ms: int = 0
    shot_distribution: dict[int, int] | None = None


def get_stats_cache_path() -> str:
    return str(Path(get_hare_config_home_dir()) / STATS_CACHE_FILENAME)


def _empty_cache() -> PersistedStatsCache:
    return PersistedStatsCache(
        shot_distribution={},
    )


def to_date_string(d: date) -> str:
    return d.isoformat()


def get_today_date_string() -> str:
    return date.today().isoformat()


def get_yesterday_date_string() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def is_date_before(a: str, b: str) -> bool:
    return a < b


async def load_stats_cache() -> PersistedStatsCache:
    fs = get_fs_implementation()
    path = get_stats_cache_path()
    try:
        content = fs.read_file_sync(path, encoding="utf-8")
        data = json_parse(content)
        if not isinstance(data, dict):
            return _empty_cache()
        if data.get("version") != STATS_CACHE_VERSION:
            log_for_debugging("Stats cache version mismatch, returning empty")
            return _empty_cache()
        return PersistedStatsCache(
            version=int(data.get("version", STATS_CACHE_VERSION)),
            last_computed_date=data.get("lastComputedDate"),
            daily_activity=[],
            daily_model_tokens=[],
            model_usage=data.get("modelUsage") or {},
            total_sessions=int(data.get("totalSessions", 0)),
            total_messages=int(data.get("totalMessages", 0)),
            longest_session=None,
            first_session_date=data.get("firstSessionDate"),
            hour_counts={},
            total_speculation_time_saved_ms=int(
                data.get("totalSpeculationTimeSavedMs", 0)
            ),
        )
    except OSError as e:
        log_for_debugging(f"Failed to load stats cache: {error_message(e)}")
        return _empty_cache()


async def save_stats_cache(cache: PersistedStatsCache) -> None:
    fs = get_fs_implementation()
    path = get_stats_cache_path()
    tmp = f"{path}.{secrets.token_hex(8)}.tmp"
    try:
        fs.mkdir(str(Path(path).parent))
    except OSError:
        pass
    try:
        payload = json_stringify(
            {
                "version": cache.version,
                "lastComputedDate": cache.last_computed_date,
                "dailyActivity": [],
                "dailyModelTokens": [],
                "modelUsage": cache.model_usage,
                "totalSessions": cache.total_sessions,
                "totalMessages": cache.total_messages,
                "longestSession": None,
                "firstSessionDate": cache.first_session_date,
                "hourCounts": cache.hour_counts,
                "totalSpeculationTimeSavedMs": cache.total_speculation_time_saved_ms,
            },
            indent=2,
        )
        Path(tmp).write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log_error(e)
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass


def merge_cache_with_new_stats(
    existing: PersistedStatsCache,
    new_stats: dict[str, Any],
    new_last_computed: str,
) -> PersistedStatsCache:
    _ = new_stats
    return PersistedStatsCache(
        version=STATS_CACHE_VERSION,
        last_computed_date=new_last_computed,
        daily_activity=list(existing.daily_activity),
        daily_model_tokens=list(existing.daily_model_tokens),
        model_usage=dict(existing.model_usage),
        total_sessions=existing.total_sessions,
        total_messages=existing.total_messages,
        longest_session=existing.longest_session,
        first_session_date=existing.first_session_date,
        hour_counts=dict(existing.hour_counts),
        total_speculation_time_saved_ms=existing.total_speculation_time_saved_ms,
        shot_distribution=dict(existing.shot_distribution or {}),
    )
