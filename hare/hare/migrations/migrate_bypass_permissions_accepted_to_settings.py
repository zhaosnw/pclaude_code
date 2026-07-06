"""Port of: src/migrations/migrateBypassPermissionsAcceptedToSettings.ts"""

from __future__ import annotations

from hare.migrations._deps import (
    get_global_config,
    log_error,
    log_event,
    save_global_config,
)
from hare.migrations._deps import update_settings_for_source


def has_skip_dangerous_mode_permission_prompt() -> bool:
    return False


def migrate_bypass_permissions_accepted_to_settings() -> None:
    g = get_global_config()
    if not g.get("bypassPermissionsModeAccepted"):
        return
    try:
        if not has_skip_dangerous_mode_permission_prompt():
            update_settings_for_source(
                "userSettings", {"skipDangerousModePermissionPrompt": True}
            )
        log_event("tengu_migrate_bypass_permissions_accepted", {})
        save_global_config(
            lambda c: {
                k: v for k, v in c.items() if k != "bypassPermissionsModeAccepted"
            }
        )
    except Exception as e:
        log_error(e)
