"""Ultra review quota API. Port of: src/services/api/ultrareviewQuota.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UltrareviewQuota:
    remaining: int = 0
    limit: int = 0


async def get_ultrareview_quota() -> UltrareviewQuota:
    return UltrareviewQuota()
