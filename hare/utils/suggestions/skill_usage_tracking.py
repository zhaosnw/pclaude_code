"""Track skill invocation counts for ranking suggestions.

Port of: src/utils/suggestions/skillUsageTracking.ts
"""

from __future__ import annotations

from collections import defaultdict

_usage: dict[str, int] = defaultdict(int)


def record_skill_usage(skill_id: str) -> None:
    _usage[skill_id] += 1


def get_skill_usage(skill_id: str) -> int:
    return _usage.get(skill_id, 0)
