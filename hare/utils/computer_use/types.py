"""
Computer use types.

Port of: src/utils/computerUse/common.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ComputerUseMcpState:
    hidden_during_turn: set[str] | None = None
    lock_held: bool = False
    session_id: str = ""


@dataclass
class ScreenshotResult:
    image_data: bytes = b""
    width: int = 0
    height: int = 0
    format: str = "png"
