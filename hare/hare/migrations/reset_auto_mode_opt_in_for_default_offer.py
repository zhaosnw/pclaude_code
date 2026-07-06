"""Port of: src/migrations/resetAutoModeOptInForDefaultOffer.ts"""

from __future__ import annotations

from hare.migrations._deps import (
    get_global_config,
    get_settings_for_source,
    log_error,
    log_event,
    save_global_config,
    update_settings_for_source,
)


def get_auto_mode_enabled_state() -> str:
    return "disabled"


def reset_auto_mode_opt_in_for_default_offer() -> None:
    if not __import__("os").environ.get("FEATURE_TRANSCRIPT_CLASSIFIER"):
        return
    config = get_global_config()
    if config.get("hasResetAutoModeOptInForDefaultOffer"):
        return
    if get_auto_mode_enabled_state() != "enabled":
        return
    try:
        user = get_settings_for_source("userSettings") or {}
        perms = user.get("permissions") or {}
        if user.get("skipAutoPermissionPrompt") and perms.get("defaultMode") != "auto":
            update_settings_for_source(
                "userSettings", {"skipAutoPermissionPrompt": None}
            )
            log_event("tengu_migrate_reset_auto_opt_in_for_default_offer", {})
        save_global_config(
            lambda c: c
            if c.get("hasResetAutoModeOptInForDefaultOffer")
            else {**c, "hasResetAutoModeOptInForDefaultOffer": True}
        )
    except Exception as e:
        log_error(e)
