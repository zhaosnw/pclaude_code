"""Detect Bun / bundled executable runtime (port of bundledMode.ts)."""

from __future__ import annotations


def is_running_with_bun() -> bool:
    return False


def is_in_bundled_mode() -> bool:
    return False
