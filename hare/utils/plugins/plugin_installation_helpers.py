"""Shared helpers for plugin install UI/CLI. Port of pluginInstallationHelpers.ts."""

from __future__ import annotations

from typing import Any


def format_install_success(_plugin_id: str, _extra: Any = None) -> str:
    return "Installed."
