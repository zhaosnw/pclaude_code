"""
Remote managed settings – fetches org-level settings from API.

Port of: src/services/remoteManagedSettings/remoteManagedSettings.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RemoteManagedSettingsService:
    _settings: dict[str, Any] = field(default_factory=dict)
    _fetched: bool = False

    async def fetch(self) -> dict[str, Any]:
        """Fetch remote settings from API. Stub."""
        self._fetched = True
        return self._settings

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    @property
    def is_fetched(self) -> bool:
        return self._fetched


_instance: RemoteManagedSettingsService | None = None


def get_remote_settings() -> RemoteManagedSettingsService:
    global _instance
    if _instance is None:
        _instance = RemoteManagedSettingsService()
    return _instance
