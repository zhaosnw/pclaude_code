"""
Activity tracking for user vs CLI active time (`activityManager.ts`).

Uses optional injectable clock and active-time counter from bootstrap state.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

T_co = TypeVar("T_co")

USER_ACTIVITY_TIMEOUT_MS = 5000


@runtime_checkable
class _ActiveTimeCounter(Protocol):
    def add(self, seconds: float, meta: dict[str, str]) -> Any: ...


def _default_get_now() -> float:
    import time

    return time.time() * 1000.0


def _default_get_active_time_counter() -> _ActiveTimeCounter | None:
    try:
        from hare.bootstrap.state import get_active_time_counter as g  # type: ignore[attr-defined]

        return g()
    except Exception:
        return None


@dataclass
class ActivityManager:
    _active_operations: set[str] = field(default_factory=set)
    _last_user_activity_time: float = 0.0
    _last_cli_recorded_time: float = 0.0
    _is_cli_active: bool = False
    _get_now: Callable[[], float] = field(default=_default_get_now)
    _get_active_time_counter: Callable[[], _ActiveTimeCounter | None] = field(
        default=_default_get_active_time_counter
    )

    def __post_init__(self) -> None:
        self._last_cli_recorded_time = self._get_now()

    @classmethod
    def get_instance(cls) -> ActivityManager:
        global _mgr_singleton
        if _mgr_singleton is None:
            _mgr_singleton = cls()
        return _mgr_singleton

    @classmethod
    def reset_instance(cls) -> None:
        global _mgr_singleton
        _mgr_singleton = None

    @classmethod
    def create_instance(
        cls,
        *,
        get_now: Callable[[], float] | None = None,
        get_active_time_counter: Callable[[], _ActiveTimeCounter | None] | None = None,
    ) -> ActivityManager:
        global _mgr_singleton
        mgr = cls(
            _get_now=get_now or _default_get_now,
            _get_active_time_counter=get_active_time_counter
            or _default_get_active_time_counter,
        )
        _mgr_singleton = mgr
        return mgr

    def record_user_activity(self) -> None:
        if not self._is_cli_active and self._last_user_activity_time != 0:
            now = self._get_now()
            since_sec = (now - self._last_user_activity_time) / 1000.0
            if since_sec > 0:
                counter = self._get_active_time_counter()
                if counter:
                    timeout_sec = USER_ACTIVITY_TIMEOUT_MS / 1000.0
                    if since_sec < timeout_sec:
                        counter.add(since_sec, {"type": "user"})
        self._last_user_activity_time = self._get_now()

    def start_cli_activity(self, operation_id: str) -> None:
        if operation_id in self._active_operations:
            self.end_cli_activity(operation_id)
        was_empty = len(self._active_operations) == 0
        self._active_operations.add(operation_id)
        if was_empty:
            self._is_cli_active = True
            self._last_cli_recorded_time = self._get_now()

    def end_cli_activity(self, operation_id: str) -> None:
        self._active_operations.discard(operation_id)
        if len(self._active_operations) == 0:
            now = self._get_now()
            since_sec = (now - self._last_cli_recorded_time) / 1000.0
            if since_sec > 0:
                counter = self._get_active_time_counter()
                if counter:
                    counter.add(since_sec, {"type": "cli"})
            self._last_cli_recorded_time = now
            self._is_cli_active = False

    async def track_operation(
        self, operation_id: str, fn: Callable[[], Awaitable[T_co]]
    ) -> T_co:
        self.start_cli_activity(operation_id)
        try:
            return await fn()
        finally:
            self.end_cli_activity(operation_id)

    def get_activity_states(self) -> dict[str, bool | int]:
        now = self._get_now()
        since_user = (now - self._last_user_activity_time) / 1000.0
        is_user_active = since_user < USER_ACTIVITY_TIMEOUT_MS / 1000.0
        return {
            "is_user_active": is_user_active,
            "is_cli_active": self._is_cli_active,
            "active_operation_count": len(self._active_operations),
        }


_mgr_singleton: ActivityManager | None = None

activity_manager = ActivityManager.get_instance()
