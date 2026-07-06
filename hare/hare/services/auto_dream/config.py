"""Auto-dream configuration. Port of: src/services/autoDream/config.ts"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AutoDreamConfig:
    enabled: bool = False
    min_idle_minutes: int = 30


def is_auto_dream_enabled() -> bool:
    """Whether background memory consolidation should run.

    Port of: src/services/autoDream/config.ts isAutoDreamEnabled()

    User setting (autoDreamEnabled in settings.json) overrides the
    GrowthBook default when explicitly set; otherwise falls through to
    tengu_onyx_plover.
    """
    # 1. User setting override (explicit autoDreamEnabled in settings.json)
    try:
        from hare.utils.settings.settings import get_initial_settings

        setting = get_initial_settings().get("autoDreamEnabled")
        if setting is not None:
            return bool(setting)
    except Exception:
        pass

    # 2. GrowthBook fallback: tengu_onyx_plover.enabled
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        gb = get_feature_value_cached_may_be_stale("tengu_onyx_plover", None)
        if isinstance(gb, dict) and gb.get("enabled") is True:
            return True
    except Exception:
        pass

    return False
