"""Port of: src/utils/plugins/ (loader subset)"""

from __future__ import annotations
import json
import os
from typing import Any, Optional


def load_plugins(plugin_dir: str = "") -> list[dict[str, Any]]:
    base = plugin_dir or os.path.join(os.path.expanduser("~"), ".hare", "plugins")
    if not os.path.isdir(base):
        return []
    plugins = []
    for name in sorted(os.listdir(base)):
        manifest_path = os.path.join(base, name, "plugin.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
                plugins.append(
                    {
                        "name": name,
                        "path": os.path.join(base, name),
                        "manifest": manifest,
                    }
                )
            except Exception:
                continue
    return plugins


def find_plugin(name: str, plugin_dir: str = "") -> Optional[dict[str, Any]]:
    for p in load_plugins(plugin_dir):
        if p["name"] == name:
            return p
    return None
