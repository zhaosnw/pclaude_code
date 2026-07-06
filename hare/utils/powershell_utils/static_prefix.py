"""
PowerShell static prefix builder.

Port of: src/utils/powershell/staticPrefix.ts
"""

from __future__ import annotations


def build_powershell_prefix(cwd: str | None = None) -> str:
    """Build environment prefix for PowerShell commands."""
    parts: list[str] = []
    if cwd:
        parts.append(f"Set-Location -Path '{cwd}'")
    return "; ".join(parts)
