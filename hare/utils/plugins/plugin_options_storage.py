"""Per-plugin user option storage (settings + secure storage). Port of pluginOptionsStorage.ts."""

from __future__ import annotations

from typing import Any


def clear_plugin_options_cache() -> None:
    pass


def get_plugin_options(_plugin_id: str) -> dict[str, Any]:
    return {}
