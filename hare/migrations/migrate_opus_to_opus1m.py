"""Port of: src/migrations/migrateOpusToOpus1m.ts"""

from __future__ import annotations

from hare.migrations._deps import (
    get_settings_for_source,
    log_event,
    update_settings_for_source,
)


def is_opus1m_merge_enabled() -> bool:
    return False


def parse_user_specified_model(m: str) -> str:
    return m


def get_default_main_loop_model_setting() -> str:
    return "opus"


def migrate_opus_to_opus1m() -> None:
    if not is_opus1m_merge_enabled():
        return
    model = (get_settings_for_source("userSettings") or {}).get("model")
    if model != "opus":
        return
    migrated = "opus[1m]"
    default = get_default_main_loop_model_setting()
    model_to_set = (
        None
        if parse_user_specified_model(migrated) == parse_user_specified_model(default)
        else migrated
    )
    update_settings_for_source("userSettings", {"model": model_to_set})
    log_event("tengu_opus_to_opus1m_migration", {})
