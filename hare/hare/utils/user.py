"""Port of: src/utils/user.ts"""

from __future__ import annotations
import os
import platform


def get_user_id() -> str:
    return os.environ.get("CLAUDE_USER_ID", "")


def get_device_id() -> str:
    return os.environ.get("CLAUDE_DEVICE_ID", "")


def get_platform_info() -> dict[str, str]:
    return {
        "os": platform.system(),
        "arch": platform.machine(),
        "version": platform.version(),
    }


def get_git_email() -> str | None:
    """Get the user's git email (P2 — stub)."""
    return None
