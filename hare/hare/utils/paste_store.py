"""In-memory store for pasted text chips. Port of: pasteStore.ts"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PasteStore:
    entries: dict[str, str] = field(default_factory=dict)

    def put(self, key: str, text: str) -> None:
        self.entries[key] = text

    def get(self, key: str) -> str | None:
        return self.entries.get(key)
