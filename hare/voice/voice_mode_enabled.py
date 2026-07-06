"""Voice mode feature gates.

Port of: src/voice/voiceModeEnabled.ts
"""

from __future__ import annotations

from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale


def is_voice_growthbook_enabled() -> bool:
    """Voice is visible unless the emergency kill-switch is enabled."""
    return not bool(
        get_feature_value_cached_may_be_stale(
            "tengu_amber_quartz_disabled",
            False,
        )
    )


def has_voice_auth() -> bool:
    """Best-effort auth check for voice availability."""
    try:
        from hare.utils.auth import (
            get_claude_ai_oauth_tokens,
            is_anthropic_auth_enabled,
        )

        if not is_anthropic_auth_enabled():
            return False
        tokens = get_claude_ai_oauth_tokens()
        return bool(
            getattr(tokens, "access_token", None) or (tokens or {}).get("accessToken")
        )
    except Exception:
        return False


def is_voice_mode_enabled() -> bool:
    return has_voice_auth() and is_voice_growthbook_enabled()
