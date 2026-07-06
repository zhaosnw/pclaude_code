"""Zip-backed plugin cache toggles. Port of zipCache.ts."""

from __future__ import annotations

import os


def is_plugin_zip_cache_enabled() -> bool:
    return os.environ.get("CLAUDE_CODE_PLUGIN_ZIP_CACHE", "").lower() in (
        "1",
        "true",
        "yes",
    )
