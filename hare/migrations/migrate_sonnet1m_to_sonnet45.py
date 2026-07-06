"""Port of: src/migrations/migrateSonnet1mToSonnet45.ts"""

from __future__ import annotations

from hare.migrations._deps import (
    get_global_config,
    get_settings_for_source,
    save_global_config,
)
from hare.migrations._deps import update_settings_for_source


def _get_main_loop_model_override() -> str | None:
    return None


def _set_main_loop_model_override(_m: str | None) -> None:
    pass


def migrate_sonnet1m_to_sonnet45() -> None:
    if get_global_config().get("sonnet1m45MigrationComplete"):
        return
    model = (get_settings_for_source("userSettings") or {}).get("model")
    if model == "sonnet[1m]":
        update_settings_for_source("userSettings", {"model": "sonnet-4-5-20250929[1m]"})
    if _get_main_loop_model_override() == "sonnet[1m]":
        _set_main_loop_model_override("sonnet-4-5-20250929[1m]")
    save_global_config(lambda c: {**c, "sonnet1m45MigrationComplete": True})
