"""
Voice streaming STT service.

Port of: src/services/voiceStreamSTT.ts
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class VoiceStreamSTT:
    _active: bool = False
    _buffer: bytes = b""

    async def start(self) -> None:
        self._active = True
        self._buffer = b""

    async def stop(self) -> str:
        self._active = False
        return ""

    async def feed_audio(self, chunk: bytes) -> str | None:
        if not self._active:
            return None
        self._buffer += chunk
        return None

    @property
    def is_active(self) -> bool:
        return self._active
