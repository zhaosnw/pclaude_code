"""Insecure plain-text credential storage (dev / fallback).

Port of: src/utils/secureStorage/plainTextStorage.ts
"""

from __future__ import annotations

from pathlib import Path


class PlainTextStorage:
    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def read(self, key: str) -> str | None:
        p = self._base / f"{key}.txt"
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    def write(self, key: str, value: str) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        (self._base / f"{key}.txt").write_text(value, encoding="utf-8")

    def delete(self, key: str) -> None:
        p = self._base / f"{key}.txt"
        if p.is_file():
            p.unlink()
