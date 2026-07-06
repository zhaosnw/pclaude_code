"""`hare update` command (port of src/cli/update.ts)."""

from __future__ import annotations

import os


async def update() -> None:
    version = os.environ.get("CLAUDE_CODE_VERSION", "2.1.88")
    print(f"Current version: {version}")
    print("Update check stub — wire to auto_updater / native installer.")
