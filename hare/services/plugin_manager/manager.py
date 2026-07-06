"""
Plugin manager – loads and manages plugins.

Port of: src/services/pluginManager/pluginManager.ts
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Plugin:
    name: str
    version: str
    description: str = ""
    enabled: bool = True
    path: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PluginManager:
    plugins: list[Plugin] = field(default_factory=list)
    _loaded: bool = False

    async def load_plugins(self, search_paths: list[str] | None = None) -> None:
        """Discover and load plugins from search paths."""
        if search_paths is None:
            search_paths = [
                os.path.join(os.getcwd(), ".hare", "plugins"),
                os.path.join(os.path.expanduser("~"), ".hare", "plugins"),
            ]
        for sp in search_paths:
            if not os.path.isdir(sp):
                continue
            for entry in os.listdir(sp):
                manifest = os.path.join(sp, entry, "manifest.json")
                if os.path.isfile(manifest):
                    try:
                        with open(manifest, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        self.plugins.append(
                            Plugin(
                                name=data.get("name", entry),
                                version=data.get("version", "0.0.0"),
                                description=data.get("description", ""),
                                path=os.path.join(sp, entry),
                                tools=data.get("tools", []),
                            )
                        )
                    except (OSError, json.JSONDecodeError):
                        pass
        self._loaded = True

    async def reload(self) -> None:
        self.plugins.clear()
        self._loaded = False
        await self.load_plugins()

    def get_plugin(self, name: str) -> Plugin | None:
        for p in self.plugins:
            if p.name == name:
                return p
        return None


_instance: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _instance
    if _instance is None:
        _instance = PluginManager()
    return _instance
