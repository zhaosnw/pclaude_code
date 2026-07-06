"""Load hooks.json from plugins. Port of loadPluginHooks.ts."""

from __future__ import annotations

from typing import Any


def clear_plugin_hook_cache() -> None:
    pass


async def prune_removed_plugin_hooks() -> None:
    pass


async def load_plugin_hooks(_plugin_root: str) -> dict[str, Any]:
    return {}
