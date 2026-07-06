"""asciinema cast generation helpers. Port of: asciicast.ts"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AsciiCastHeader:
    version: int = 2
    width: int = 80
    height: int = 24


def emit_cast_line(timestamp: float, event_type: str, data: str) -> list[Any]:
    return [timestamp, event_type, data]
