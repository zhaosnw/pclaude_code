"""Port of: src/migrations/resetProToOpusDefault.ts"""

from __future__ import annotations

from hare.migrations._deps import (
    get_api_provider,
    get_global_config,
    get_settings_for_source,
    log_event,
    save_global_config,
    is_pro_subscriber,
)


def reset_pro_to_opus_default() -> None:
    config = get_global_config()
    if config.get("opusProMigrationComplete"):
        return
    if get_api_provider() != "firstParty" or not is_pro_subscriber():
        save_global_config(lambda c: {**c, "opusProMigrationComplete": True})
        log_event("tengu_reset_pro_to_opus_default", {"skipped": True})
        return
    settings = get_settings_for_source("userSettings") or {}
    if settings.get("model") is None:
        ts = __import__("time").time() * 1000
        save_global_config(
            lambda c: {
                **c,
                "opusProMigrationComplete": True,
                "opusProMigrationTimestamp": int(ts),
            }
        )
        log_event(
            "tengu_reset_pro_to_opus_default",
            {"skipped": False, "had_custom_model": False},
        )
    else:
        save_global_config(lambda c: {**c, "opusProMigrationComplete": True})
        log_event(
            "tengu_reset_pro_to_opus_default",
            {"skipped": False, "had_custom_model": True},
        )
