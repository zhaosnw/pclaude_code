"""Directory path completions.

Port of: src/utils/suggestions/directoryCompletion.ts
"""

from __future__ import annotations

from pathlib import Path


def complete_directory_prefix(prefix: str, cwd: Path | None = None) -> list[str]:
    base = cwd or Path.cwd()
    p = (base / prefix).expanduser() if prefix else base
    if prefix.endswith("/") or not prefix:
        search = p if p.is_dir() else p.parent
    else:
        search = p.parent if p.suffix else p
    if not search.is_dir():
        return []
    try:
        return [str(x.name) for x in search.iterdir() if x.is_dir()]
    except OSError:
        return []
