"""Port of: src/utils/effort.ts"""

from __future__ import annotations
from typing import Literal

EffortValue = Literal["low", "medium", "high"]
_effort: EffortValue = "high"


def get_effort() -> EffortValue:
    return _effort


def set_effort(value: EffortValue) -> None:
    global _effort
    _effort = value
