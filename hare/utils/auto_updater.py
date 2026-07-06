"""Auto-update checks (Electron / native). Port of: autoUpdater.ts"""

from __future__ import annotations


async def check_for_updates() -> dict[str, object]:
    return {"updateAvailable": False}
