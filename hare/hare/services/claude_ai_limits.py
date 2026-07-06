"""Hare.ai subscription limits snapshot. Port of: src/services/claudeAiLimits.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HareAiLimits:
    max_tokens_per_day: int | None = None


async def fetch_hare_ai_limits() -> HareAiLimits:
    return HareAiLimits()
