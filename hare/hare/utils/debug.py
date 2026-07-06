"""
Debug utilities.

Port of: src/utils/debug.ts
"""

from __future__ import annotations

import os
import sys


def log_for_debugging(message: str, *, level: str | None = None) -> None:
    """Log a debug message if verbose mode is enabled. `level` is accepted for API parity."""
    if os.environ.get("CLAUDE_CODE_DEBUG") == "1":
        prefix = f"[{level.upper()}] " if level else "[DEBUG] "
        print(f"{prefix}{message}", file=sys.stderr)


def log_error(error: Exception) -> None:
    """Log an error for debugging."""
    if os.environ.get("CLAUDE_CODE_DEBUG") == "1":
        print(f"[ERROR] {error}", file=sys.stderr)
