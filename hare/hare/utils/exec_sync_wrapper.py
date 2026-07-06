"""
Slow-logging wrapper around synchronous shell execution.

Port of: src/utils/execSyncWrapper.ts
"""

from __future__ import annotations

import subprocess
from typing import Any

from hare.utils.debug import log_for_debugging


def exec_sync_deprecated(
    command: str, options: dict[str, Any] | None = None
) -> str | bytes:
    """Deprecated: use async subprocess helpers where possible."""
    log_for_debugging(f"execSync: {command[:100]}")
    opts = dict(options or {})
    return (
        subprocess.run(command, shell=True, capture_output=True, **opts).stdout or b""  # nosec B602
    )
