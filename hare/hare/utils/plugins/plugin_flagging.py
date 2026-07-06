"""Persist flagged (delisted) plugins. Port of pluginFlagging.ts."""

from __future__ import annotations

_flagged: dict[str, bool] = {}


async def load_flagged_plugins() -> None:
    pass


def get_flagged_plugins() -> dict[str, bool]:
    return dict(_flagged)


async def add_flagged_plugin(plugin_id: str) -> None:
    _flagged[plugin_id] = True
