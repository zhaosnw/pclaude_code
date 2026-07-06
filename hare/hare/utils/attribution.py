"""Telemetry / attribution helpers. Port of: attribution.ts"""

from __future__ import annotations


def attribution_tag(source: str) -> str:
    return f"attr:{source}"
