"""
Factory for commands migrated to plugins.

Port of: src/commands/createMovedToPluginCommand.ts
"""

from __future__ import annotations

from typing import Any, Callable


def create_moved_to_plugin_command(
    name: str,
    plugin_id: str,
) -> Callable[[list[str]], Any]:
    def _stub(argv: list[str]) -> dict[str, Any]:
        return {"moved_to_plugin": plugin_id, "name": name, "argv": argv}

    return _stub
