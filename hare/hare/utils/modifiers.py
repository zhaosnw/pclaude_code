"""
Keyboard modifier detection (macOS native). Port of src/utils/modifiers.ts.
"""

from __future__ import annotations

import sys
from typing import Literal

ModifierKey = Literal["shift", "command", "control", "option"]

_prewarmed = False


def prewarm_modifiers() -> None:
    """Pre-load native module on darwin to reduce first-call latency."""
    global _prewarmed
    if _prewarmed or sys.platform != "darwin":
        return
    _prewarmed = True
    try:
        import modifiers_napi  # type: ignore[import-untyped]

        modifiers_napi.prewarm()
    except Exception:
        pass


def is_modifier_pressed(modifier: ModifierKey) -> bool:
    """Return True if the given modifier is currently held (darwin only)."""
    if sys.platform != "darwin":
        return False
    try:
        import modifiers_napi  # type: ignore[import-untyped]

        return bool(modifiers_napi.is_modifier_pressed(modifier))
    except Exception:
        return False
