"""
Glob utilities.

Port of: src/utils/glob.ts

Wraps Python's glob module with permission filtering.
"""

from __future__ import annotations

import glob as _glob_module
import os
import re
from typing import Any, Optional

from hare.app_types.permissions import ToolPermissionContext
from hare.utils.platform import get_platform
from hare.utils.ripgrep import rip_grep


def extract_glob_base_directory(pattern: str) -> dict[str, str]:
    """Static directory before first glob metachar; mirrors TS extractGlobBaseDirectory."""
    glob_chars = re.compile(r"[*?[{]")
    m = glob_chars.search(pattern)
    if not m:
        d = os.path.dirname(pattern) or "."
        f = os.path.basename(pattern)
        return {"base_dir": d, "relative_pattern": f}
    static_prefix = pattern[: m.start()]
    last_sep = max(static_prefix.rfind("/"), static_prefix.rfind(os.sep))
    if last_sep == -1:
        return {"base_dir": "", "relative_pattern": pattern}
    base_dir = static_prefix[:last_sep]
    relative_pattern = pattern[last_sep + 1 :]
    if base_dir == "" and last_sep == 0:
        base_dir = "/"
    if get_platform() == "windows" and re.match(r"^[A-Za-z]:$", base_dir):
        base_dir = base_dir + os.sep
    return {"base_dir": base_dir, "relative_pattern": relative_pattern}


async def glob_files_ripgrep(
    file_pattern: str,
    cwd: str,
    limit: int,
    offset: int,
    abort_signal: Any,
    _tool_permission_context: ToolPermissionContext,
) -> dict[str, Any]:
    """Ripgrep-backed glob (memory-friendly) — stub ignores/env parity from TS."""
    del _tool_permission_context
    search_dir = cwd
    search_pattern = file_pattern
    if os.path.isabs(file_pattern):
        ex = extract_glob_base_directory(file_pattern)
        if ex["base_dir"]:
            search_dir = ex["base_dir"]
            search_pattern = ex["relative_pattern"]
    args = [
        "--files",
        "--glob",
        search_pattern,
        "--sort=modified",
        "--no-ignore",
        "--hidden",
    ]
    all_paths = await rip_grep(args, search_dir, abort_signal)
    absolute_paths = [
        p if os.path.isabs(p) else os.path.join(search_dir, p) for p in all_paths
    ]
    truncated = len(absolute_paths) > offset + limit
    files = absolute_paths[offset : offset + limit]
    return {"files": files, "truncated": truncated}


async def glob(
    pattern: str,
    base_path: str,
    options: dict[str, Any] | None = None,
    signal: Any = None,
    permission_context: Optional[ToolPermissionContext] = None,
) -> dict[str, Any]:
    """
    Search for files matching a glob pattern.
    Returns {"files": [...], "truncated": bool}.
    """
    opts = options or {}
    limit = opts.get("limit", 100)
    offset = opts.get("offset", 0)

    # Build full pattern
    if not pattern.startswith("**/") and not os.path.isabs(pattern):
        full_pattern = os.path.join(base_path, "**", pattern)
    else:
        full_pattern = os.path.join(base_path, pattern)

    matches = _glob_module.glob(full_pattern, recursive=True)

    # Filter to files only
    files = [f for f in matches if os.path.isfile(f)]

    # Sort by mtime (newest first)
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)

    # Apply offset
    files = files[offset:]

    # Check truncation
    truncated = len(files) > limit
    files = files[:limit]

    return {"files": files, "truncated": truncated}
