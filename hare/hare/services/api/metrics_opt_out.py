"""Metrics opt-out preference sync. Port of: src/services/api/metricsOptOut.ts"""

from __future__ import annotations


async def get_metrics_opt_out() -> bool:
    return False


async def set_metrics_opt_out(_value: bool) -> None:
    return
