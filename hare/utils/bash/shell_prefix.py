"""Port of: src/utils/bash/shellPrefix.ts + prefix.ts"""

from __future__ import annotations


def get_bash_prefix(env: dict[str, str] | None = None) -> str:
    if not env:
        return ""
    parts = [f"export {k}={v}" for k, v in env.items()]
    return " && ".join(parts) + " && " if parts else ""
