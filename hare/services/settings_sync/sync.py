"""Port of: src/services/settingsSync/"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SettingsSyncState:
    last_synced: float = 0.0
    version: int = 0
    dirty: bool = False


async def sync_settings(state: Optional[SettingsSyncState] = None) -> SettingsSyncState:
    """Sync settings with remote (stub)."""
    return state or SettingsSyncState()


async def push_settings(settings: dict[str, Any]) -> bool:
    return False


async def pull_settings() -> Optional[dict[str, Any]]:
    return None
