"""Port of: src/migrations/migrateAutoUpdatesToSettings.ts"""

from __future__ import annotations

import os

from hare.migrations._deps import (
    get_global_config,
    log_error,
    log_event,
    save_global_config,
)
from hare.migrations._deps import get_settings_for_source, update_settings_for_source


def migrate_auto_updates_to_settings() -> None:
    g = get_global_config()
    if (
        g.get("autoUpdates") is not False
        or g.get("autoUpdatesProtectedForNative") is True
    ):
        return
    try:
        user = dict(get_settings_for_source("userSettings") or {})
        env = dict(user.get("env") or {})
        env["DISABLE_AUTOUPDATER"] = "1"
        update_settings_for_source("userSettings", {**user, "env": env})
        log_event(
            "tengu_migrate_autoupdates_to_settings",
            {
                "was_user_preference": True,
                "already_had_env_var": bool(
                    (user.get("env") or {}).get("DISABLE_AUTOUPDATER")
                ),
            },
        )
        os.environ["DISABLE_AUTOUPDATER"] = "1"
        save_global_config(
            lambda c: {
                k: v
                for k, v in c.items()
                if k not in ("autoUpdates", "autoUpdatesProtectedForNative")
            }
        )
    except Exception as e:
        log_error(e)
        log_event("tengu_migrate_autoupdates_error", {"has_error": True})
