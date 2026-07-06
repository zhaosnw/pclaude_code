"""User-Agent string for Hare (port of userAgent.ts)."""

from __future__ import annotations

_VERSION = "2.1.88"


def get_hare_code_user_agent() -> str:
    return f"hare-code/{_VERSION}"
