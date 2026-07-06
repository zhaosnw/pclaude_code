"""Storage that tries keychain then falls back to encrypted/plain files.

Port of: src/utils/secureStorage/fallbackStorage.ts
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SecureStorage(Protocol):
    def read(self, key: str) -> str | None: ...
    def write(self, key: str, value: str) -> None: ...


def build_fallback_storage(
    _preferred: SecureStorage, _fallback_dir: Path
) -> SecureStorage:
    """Compose primary + fallback (stub returns preferred)."""
    return _preferred
