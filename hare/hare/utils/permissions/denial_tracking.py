"""Track permission denials for UX. Port of denialTracking.ts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DenialStats:
    count_by_tool: dict[str, int] = field(default_factory=dict)


_stats = DenialStats()


def record_denial(tool_name: str) -> None:
    _stats.count_by_tool[tool_name] = _stats.count_by_tool.get(tool_name, 0) + 1


def get_denial_stats() -> DenialStats:
    return _stats
