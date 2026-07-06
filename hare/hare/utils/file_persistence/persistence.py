"""
File persistence – upload modified files to Files API.

Port of: src/utils/filePersistence/filePersistence.ts
"""

from __future__ import annotations

import os
from typing import Any


def is_file_persistence_enabled() -> bool:
    return os.environ.get("CLAUDE_CODE_ENVIRONMENT_KIND") == "byoc" and bool(
        os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
    )


async def run_file_persistence(turn_start_time: float) -> dict[str, Any] | None:
    """Execute file persistence for modified files. Stub."""
    if not is_file_persistence_enabled():
        return None
    return None
