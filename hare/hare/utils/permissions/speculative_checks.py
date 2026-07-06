"""Port of: src/utils/permissions/speculativeChecks.ts"""

from __future__ import annotations

_speculative: dict[str, bool] = {}


def add_speculative_check(key: str, allowed: bool) -> None:
    _speculative[key] = allowed


def get_speculative_check(key: str) -> bool | None:
    return _speculative.get(key)


def clear_speculative_checks() -> None:
    _speculative.clear()
