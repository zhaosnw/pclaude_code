"""
Validation for /add-dir paths.

Port of: src/commands/add-dir/validation.ts
"""

from __future__ import annotations

from pathlib import Path


def validate_add_dir_path(raw: str, workspace: Path) -> tuple[bool, str]:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = workspace / p
    try:
        p.resolve().relative_to(workspace.resolve())
    except ValueError:
        return False, "Path escapes workspace"
    return True, ""
