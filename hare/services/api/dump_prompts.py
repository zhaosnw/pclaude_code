"""Debug: dump prompts to disk. Port of: src/services/api/dumpPrompts.ts"""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def dump_prompts_to_dir(_messages: list[dict[str, Any]], _target: Path) -> None:
    return
