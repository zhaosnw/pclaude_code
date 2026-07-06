"""
Voice service – handles voice input/output.

Port of: src/services/voice/voiceService.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VoiceService:
    _active: bool = False

    async def start(self) -> None:
        """Start voice service. Stub."""
        self._active = True

    async def stop(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    async def process_audio(self, audio_data: bytes) -> str:
        """Process audio input. Stub."""
        return ""
