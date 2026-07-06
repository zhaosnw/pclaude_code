"""Port of: src/migrations/migrateLegacyOpusToCurrent.ts"""

from __future__ import annotations

import time

from hare.migrations._deps import (
    get_api_provider,
    get_settings_for_source,
    log_event,
    save_global_config,
    update_settings_for_source,
)


def is_legacy_model_remap_enabled() -> bool:
    return True


_LEGACY = frozenset(
    {
        "claude-opus-4-20250514",
        "claude-opus-4-1-20250805",
        "claude-opus-4-0",
        "claude-opus-4-1",
    }
)


def migrate_legacy_opus_to_current() -> None:
    if get_api_provider() != "firstParty":
        return
    if not is_legacy_model_remap_enabled():
        return
    model = (get_settings_for_source("userSettings") or {}).get("model")
    if model not in _LEGACY:
        return
    update_settings_for_source("userSettings", {"model": "opus"})
    save_global_config(
        lambda c: {**c, "legacyOpusMigrationTimestamp": int(time.time() * 1000)}
    )
    log_event("tengu_legacy_opus_migration", {"from_model": model})
