"""
Teleport environment selection.

Port of: src/utils/teleport/environmentSelection.ts + environments.ts
"""

from __future__ import annotations

import os


def get_environment_kind() -> str:
    return os.environ.get("CLAUDE_CODE_ENVIRONMENT_KIND", "local")


def is_remote_session() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID"))


def get_remote_session_id() -> str | None:
    return os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
