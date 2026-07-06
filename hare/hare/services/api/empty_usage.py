"""Empty usage placeholder for offline / mock. Port of: src/services/api/emptyUsage.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0


def empty_usage() -> UsageSnapshot:
    return UsageSnapshot()
