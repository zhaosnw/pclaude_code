"""
Build-time / bundle feature flags.

Port of: bun:bundle `feature()` in recovered TS sources.
"""

from __future__ import annotations

import os


def feature(name: str) -> bool:
    """
    Returns True when the named bundle feature is compiled in and enabled.
    Stub: enable per-flag via CLAUDE_CODE_BUNDLE_FEATURE_<NAME>=1.
    """
    key = f"CLAUDE_CODE_BUNDLE_FEATURE_{name.upper()}"
    return os.environ.get(key, "").lower() in ("1", "true", "yes")
