"""Port of: src/migrations/migrateFennecToOpus.ts"""

from __future__ import annotations

import os

from hare.migrations._deps import get_settings_for_source, update_settings_for_source


def migrate_fennec_to_opus() -> None:
    if os.environ.get("USER_TYPE") != "ant":
        return
    settings = get_settings_for_source("userSettings") or {}
    model = settings.get("model")
    if not isinstance(model, str):
        return
    if model.startswith("fennec-latest[1m]"):
        update_settings_for_source("userSettings", {"model": "opus[1m]"})
    elif model.startswith("fennec-latest"):
        update_settings_for_source("userSettings", {"model": "opus"})
    elif model.startswith("fennec-fast-latest") or model.startswith("opus-4-5-fast"):
        update_settings_for_source(
            "userSettings", {"model": "opus[1m]", "fastMode": True}
        )
