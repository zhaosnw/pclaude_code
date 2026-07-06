"""Paste images from clipboard into prompts. Port of: imagePaste.ts"""

from __future__ import annotations

from pathlib import Path


async def read_clipboard_image_bytes() -> bytes | None:
    return None


async def save_pasted_image(_dest: Path) -> bool:
    return False
