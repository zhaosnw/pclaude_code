"""Plugin lifecycle telemetry field builders.

Port of: src/utils/telemetry/pluginTelemetry.ts
"""

from __future__ import annotations

import hashlib
from typing import Any

PLUGIN_ID_HASH_SALT = "hare-plugin-telemetry-v1"
BUILTIN_MARKETPLACE_NAME = "builtin"


def hash_plugin_id(name: str, marketplace: str | None = None) -> str:
    key = f"{name}@{marketplace.lower()}" if marketplace else name
    digest = hashlib.sha256((key + PLUGIN_ID_HASH_SALT).encode()).hexdigest()
    return digest[:16]


def log_plugin_enabled(_plugin: dict[str, Any]) -> None:
    pass


def log_plugin_error(_err: Any) -> None:
    pass
