"""Port of: src/migrations/migrateReplBridgeEnabledToRemoteControlAtStartup.ts"""

from __future__ import annotations

from hare.migrations._deps import save_global_config


def migrate_repl_bridge_enabled_to_remote_control_at_startup() -> None:
    def updater(prev: dict) -> dict:
        old = prev.get("replBridgeEnabled")
        if old is None:
            return prev
        if prev.get("remoteControlAtStartup") is not None:
            return prev
        nxt = {**prev, "remoteControlAtStartup": bool(old)}
        nxt.pop("replBridgeEnabled", None)
        return nxt

    save_global_config(updater)
