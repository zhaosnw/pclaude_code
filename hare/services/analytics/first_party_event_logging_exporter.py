"""Exporter for first-party event logging pipeline. Port of: src/services/analytics/firstPartyEventLoggingExporter.ts"""

from __future__ import annotations

from typing import Any


async def export_first_party_events(_batch: list[dict[str, Any]]) -> None:
    return
