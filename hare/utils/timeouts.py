"""Timeout constants for bash and tools (port of timeouts.ts)."""

from __future__ import annotations

import os

_DEFAULT_TIMEOUT_MS = 120_000
_MAX_TIMEOUT_MS = 600_000


def get_default_bash_timeout_ms(
    env: dict[str, str | None] | None = None,
) -> int:
    e = env if env is not None else dict(os.environ)
    raw = e.get("BASH_DEFAULT_TIMEOUT_MS")
    if raw:
        try:
            parsed = int(raw, 10)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_MS


def get_max_bash_timeout_ms(env: dict[str, str | None] | None = None) -> int:
    e = env if env is not None else dict(os.environ)
    raw = e.get("BASH_MAX_TIMEOUT_MS")
    default = get_default_bash_timeout_ms(e)
    if raw:
        try:
            parsed = int(raw, 10)
            if parsed > 0:
                return max(parsed, default)
        except ValueError:
            pass
    return max(_MAX_TIMEOUT_MS, default)
