"""
VCR recorder – record and replay API conversations.

Port of: src/services/vcr.ts
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VCREntry:
    timestamp: float
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class VCRRecorder:
    entries: list[VCREntry] = field(default_factory=list)
    _recording: bool = False
    _path: str = ""

    def start_recording(self, path: str = "") -> None:
        self._recording = True
        self._path = path
        self.entries.clear()

    def stop_recording(self) -> list[VCREntry]:
        self._recording = False
        if self._path:
            self.save(self._path)
        return list(self.entries)

    def record(self, entry_type: str, data: dict[str, Any]) -> None:
        if not self._recording:
            return
        self.entries.append(VCREntry(timestamp=time.time(), type=entry_type, data=data))

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {"t": e.timestamp, "type": e.type, "data": e.data}
                    for e in self.entries
                ],
                f,
            )

    @classmethod
    def load(cls, path: str) -> "VCRRecorder":
        recorder = cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            recorder.entries.append(
                VCREntry(
                    timestamp=entry.get("t", 0),
                    type=entry.get("type", ""),
                    data=entry.get("data", {}),
                )
            )
        return recorder

    @property
    def is_recording(self) -> bool:
        return self._recording
