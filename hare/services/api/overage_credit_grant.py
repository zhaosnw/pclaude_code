"""Overage credit grant API. Port of: src/services/api/overageCreditGrant.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OverageGrant:
    granted: bool = False


async def request_overage_credit() -> OverageGrant:
    return OverageGrant()
