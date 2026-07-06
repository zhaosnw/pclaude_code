"""Port of: src/migrations/migrateEnableAllProjectMcpServersToSettings.ts"""

from __future__ import annotations

from hare.migrations._deps import (
    get_current_project_config,
    get_settings_for_source,
    log_error,
    log_event,
    save_current_project_config,
    update_settings_for_source,
)


def migrate_enable_all_project_mcp_servers_to_settings() -> None:
    pc = get_current_project_config()
    has_enable = pc.get("enableAllProjectMcpServers") is not None
    has_enabled = bool(pc.get("enabledMcpjsonServers"))
    has_disabled = bool(pc.get("disabledMcpjsonServers"))
    if not has_enable and not has_enabled and not has_disabled:
        return
    try:
        existing = dict(get_settings_for_source("localSettings") or {})
        updates: dict = {}
        fields_remove: list[str] = []
        if has_enable and existing.get("enableAllProjectMcpServers") is None:
            updates["enableAllProjectMcpServers"] = pc.get("enableAllProjectMcpServers")
            fields_remove.append("enableAllProjectMcpServers")
        elif has_enable:
            fields_remove.append("enableAllProjectMcpServers")
        if has_enabled and pc.get("enabledMcpjsonServers"):
            ex = list(existing.get("enabledMcpjsonServers") or [])
            updates["enabledMcpjsonServers"] = list(
                dict.fromkeys([*ex, *pc["enabledMcpjsonServers"]])
            )
            fields_remove.append("enabledMcpjsonServers")
        if has_disabled and pc.get("disabledMcpjsonServers"):
            ex = list(existing.get("disabledMcpjsonServers") or [])
            updates["disabledMcpjsonServers"] = list(
                dict.fromkeys([*ex, *pc["disabledMcpjsonServers"]])
            )
            fields_remove.append("disabledMcpjsonServers")
        if updates:
            update_settings_for_source("localSettings", updates)
        if fields_remove:
            save_current_project_config(
                lambda c: {k: v for k, v in c.items() if k not in set(fields_remove)}
            )
        log_event(
            "tengu_migrate_mcp_approval_fields_success",
            {"migratedCount": len(fields_remove)},
        )
    except Exception as e:
        log_error(e)
        log_event("tengu_migrate_mcp_approval_fields_error", {})
