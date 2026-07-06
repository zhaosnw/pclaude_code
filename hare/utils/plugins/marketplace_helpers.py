"""Marketplace URL/display helpers. Port of marketplaceHelpers.ts."""

from __future__ import annotations


def format_marketplace_source_for_display(source: dict[str, object]) -> str:
    return str(source.get("source", ""))
