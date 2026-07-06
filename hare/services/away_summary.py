"""Summarize session when user is away. Port of: src/services/awaySummary.ts"""

from __future__ import annotations

from typing import Any


async def maybe_generate_away_summary(_messages: list[dict[str, Any]]) -> str | None:
    return None
