"""Launch terminal apps from deep links.

Port of: src/utils/deepLink/terminalLauncher.ts
"""

from __future__ import annotations

from pathlib import Path


async def launch_terminal_at_path(_path: Path, *, profile: str | None = None) -> bool:
    del profile
    return False
