"""
Plugin loader.

Port of: src/plugins/pluginLoader.ts
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginDefinition:
    name: str
    version: str = "0.0.0"
    description: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    entry_point: str | None = None


def load_plugin(plugin_path: str) -> PluginDefinition | None:
    """Load a plugin from its directory."""
    manifest_path = os.path.join(plugin_path, "manifest.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return PluginDefinition(
            name=data.get("name", os.path.basename(plugin_path)),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            tools=data.get("tools", []),
            prompts=data.get("prompts", []),
            resources=data.get("resources", []),
            entry_point=data.get("entryPoint"),
        )
    except (OSError, json.JSONDecodeError):
        return None
