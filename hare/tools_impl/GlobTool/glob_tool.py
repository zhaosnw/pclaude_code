"""
GlobTool – fast file pattern matching with permission filtering.

Port of: src/tools/GlobTool/GlobTool.ts
"""

from __future__ import annotations
import glob as _glob
import os
import time
from pathlib import Path
from typing import Any

TOOL_NAME = "Glob"
GLOB_TOOL_NAME = TOOL_NAME
MAX_RESULTS = 500


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g., '**/*.py', 'src/**/*.ts'). Supports brace expansion.",
            },
            "path": {
                "type": "string",
                "description": "Base directory to search in (default: current working directory)",
            },
        },
        "required": ["pattern"],
    }


def is_read_only(input: dict[str, Any]) -> bool:
    return True


def is_concurrency_safe(input: dict[str, Any]) -> bool:
    return True


async def call(pattern: str, path: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Find files matching a glob pattern.

    Results are sorted by modification time (newest first), limited
    to MAX_RESULTS, and returned as relative paths from the base directory.
    Only files are returned (directories are filtered out).
    """
    base = Path(path).resolve() if path else Path.cwd()
    if not base.exists() or not base.is_dir():
        return {"error": f"Directory not found: {base}"}

    full_pattern = str(base / pattern)

    try:
        matches = _glob.glob(full_pattern, recursive=True)
    except Exception as e:
        return {"error": f"Glob error: {e}"}

    # Filter to files only, sort by mtime (newest first)
    file_matches: list[tuple[str, float]] = []
    for m in matches:
        if os.path.isfile(m):
            try:
                mtime = os.path.getmtime(m)
            except OSError:
                mtime = 0.0
            file_matches.append((m, mtime))

    file_matches.sort(key=lambda x: x[1], reverse=True)

    truncated = len(file_matches) > MAX_RESULTS
    file_matches = file_matches[:MAX_RESULTS]

    # Convert to relative paths
    relative = []
    for abs_path, mtime in file_matches:
        try:
            rel = str(Path(abs_path).relative_to(base))
            relative.append(rel.replace("\\", "/"))
        except ValueError:
            relative.append(abs_path.replace("\\", "/"))

    # Apply permission-based ignore patterns if available
    ignore_patterns = kwargs.get("_ignore_patterns") or kwargs.get("ignore_patterns")
    if ignore_patterns and isinstance(ignore_patterns, list):
        import fnmatch
        relative = [
            r for r in relative
            if not any(fnmatch.fnmatch(r, pat) for pat in ignore_patterns)
        ]

    return {
        "files": relative,
        "count": len(relative),
        "truncated": truncated,
        "totalFound": len(file_matches),
        "basePath": str(base),
    }
