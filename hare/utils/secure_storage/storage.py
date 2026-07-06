"""
Secure storage implementation – plaintext fallback.

Port of: src/utils/secureStorage/index.ts + plainTextStorage.ts
"""

from __future__ import annotations

import json
from pathlib import Path

_STORAGE_DIR = Path.home() / ".hare" / "credentials"


class SecureStorage:
    """Simple file-based credential storage (plaintext fallback)."""

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._dir = storage_dir or _STORAGE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "store.json"
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            try:
                self._data = json.loads(self._file.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _save(self) -> None:
        self._file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value
        self._save()

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._save()


_instance: SecureStorage | None = None


def get_secure_storage() -> SecureStorage:
    global _instance
    if _instance is None:
        _instance = SecureStorage()
    return _instance


def get_item(key: str) -> str | None:
    return get_secure_storage().get(key)


def set_item(key: str, value: str) -> None:
    get_secure_storage().set(key, value)


def delete_item(key: str) -> None:
    get_secure_storage().delete(key)
