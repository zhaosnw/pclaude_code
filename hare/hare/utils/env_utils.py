"""
Environment variable utilities.

Port of: src/utils/envUtils.ts
"""

from __future__ import annotations

import os
from pathlib import Path


def is_env_truthy(value: str | None) -> bool:
    """Check if an environment variable value is truthy."""
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes")


def is_env_defined_falsy(value: str | None) -> bool:
    """Explicit false/0/no."""
    if value is None:
        return False
    return value.lower() in ("0", "false", "no")


def is_bare_mode() -> bool:
    """Check if running in bare/simple mode."""
    return is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE"))


def get_hare_config_home_dir() -> str:
    """User config directory (~/.hare by default, overridable via HARE_CONFIG_DIR)."""
    return os.environ.get("HARE_CONFIG_DIR") or str(Path.home() / ".hare")


def has_node_option(flag: str) -> bool:
    """True if `flag` appears in NODE_OPTIONS or sys.argv (Node-compat)."""
    import shlex
    import sys

    opts = os.environ.get("NODE_OPTIONS", "")
    try:
        parts = shlex.split(opts)
    except ValueError:
        parts = opts.split()
    if flag in parts:
        return True
    return flag in sys.argv
