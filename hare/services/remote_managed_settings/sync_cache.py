"""Disk cache for remote managed settings. Port of: src/services/remoteManagedSettings/syncCache.ts"""

from __future__ import annotations

from pathlib import Path

from hare.services.remote_managed_settings.types import RemoteManagedSettingsPayload


async def read_sync_cache(_path: Path) -> RemoteManagedSettingsPayload | None:
    return None


async def write_sync_cache(_path: Path, _payload: RemoteManagedSettingsPayload) -> None:
    return


def is_remote_managed_settings_eligible() -> bool:
    """Check if remote managed settings are eligible (P2 — stub)."""
    return False
