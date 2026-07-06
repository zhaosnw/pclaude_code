"""Port of: src/migrations/migrateSonnet45ToSonnet46.ts"""

from __future__ import annotations

from hare.migrations._deps import (
    get_api_provider,
    get_global_config,
    get_settings_for_source,
    log_event,
    save_global_config,
    is_max_subscriber,
    is_pro_subscriber,
    is_team_premium_subscriber,
    update_settings_for_source,
)

_SONNET_45 = frozenset(
    {
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-5-20250929[1m]",
        "sonnet-4-5-20250929",
        "sonnet-4-5-20250929[1m]",
    }
)


def migrate_sonnet45_to_sonnet46() -> None:
    if get_api_provider() != "firstParty":
        return
    if not (is_pro_subscriber() or is_max_subscriber() or is_team_premium_subscriber()):
        return
    model = (get_settings_for_source("userSettings") or {}).get("model")
    if model not in _SONNET_45:
        return
    has_1m = isinstance(model, str) and model.endswith("[1m]")
    update_settings_for_source(
        "userSettings", {"model": "sonnet[1m]" if has_1m else "sonnet"}
    )
    cfg = get_global_config()
    if (cfg.get("numStartups") or 0) > 1:
        import time

        save_global_config(
            lambda c: {**c, "sonnet45To46MigrationTimestamp": int(time.time() * 1000)}
        )
    log_event("tengu_sonnet45_to_46_migration", {"from_model": model, "has_1m": has_1m})
