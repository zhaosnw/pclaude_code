"""Merge --plugin-dir session plugins into settings cascade. Port of addDirPluginSettings.ts."""

from __future__ import annotations

from typing import Any


def apply_dir_plugin_settings(_settings: dict[str, Any]) -> dict[str, Any]:
    return dict(_settings)
