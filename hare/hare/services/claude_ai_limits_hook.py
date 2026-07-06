"""Hook when limits change mid-session. Port of: src/services/claudeAiLimitsHook.ts"""

from __future__ import annotations

from typing import Callable


def register_limits_hook(_cb: Callable[[], None]) -> Callable[[], None]:
    return lambda: None
