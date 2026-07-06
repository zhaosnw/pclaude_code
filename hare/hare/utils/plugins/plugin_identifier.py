"""
Plugin identifier parsing and scope mapping.

Port of: src/utils/plugins/pluginIdentifier.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hare.utils.plugins.schemas import ALLOWED_OFFICIAL_MARKETPLACE_NAMES
from hare.utils.settings.constants import EditableSettingSource, SettingSource

PluginScope = Literal["managed", "user", "project", "local"]
ExtendedPluginScope = Literal["managed", "user", "project", "local", "flag"]

SETTING_SOURCE_TO_SCOPE: dict[SettingSource, ExtendedPluginScope] = {
    "policySettings": "managed",
    "userSettings": "user",
    "projectSettings": "project",
    "localSettings": "local",
    "flagSettings": "flag",
}

_SCOPE_TO_EDITABLE_SOURCE: dict[
    Literal["user", "project", "local"], EditableSettingSource
] = {
    "user": "userSettings",
    "project": "projectSettings",
    "local": "localSettings",
}


@dataclass
class ParsedPluginIdentifier:
    name: str
    marketplace: str | None = None


def parse_plugin_identifier(plugin: str) -> ParsedPluginIdentifier:
    if "@" in plugin:
        parts = plugin.split("@", 1)
        return ParsedPluginIdentifier(name=parts[0] or "", marketplace=parts[1])
    return ParsedPluginIdentifier(name=plugin)


def build_plugin_id(name: str, marketplace: str | None = None) -> str:
    return f"{name}@{marketplace}" if marketplace else name


def is_official_marketplace_name(marketplace: str | None) -> bool:
    return (
        marketplace is not None
        and marketplace.lower() in ALLOWED_OFFICIAL_MARKETPLACE_NAMES
    )


def scope_to_setting_source(scope: PluginScope) -> EditableSettingSource:
    if scope == "managed":
        raise ValueError("Cannot install plugins to managed scope")
    return _SCOPE_TO_EDITABLE_SOURCE[scope]


def setting_source_to_scope(
    source: EditableSettingSource,
) -> Literal["user", "project", "local"]:
    v = SETTING_SOURCE_TO_SCOPE.get(source)  # type: ignore[arg-type]
    if v in ("user", "project", "local"):
        return v
    raise KeyError(source)
