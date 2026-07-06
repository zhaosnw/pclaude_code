"""
Bridge pointer — crash-recovery file for Remote Control sessions.

Port of: src/bridge/bridgePointer.ts

Written immediately after bridge session creation, periodically refreshed,
cleared on clean shutdown. If process dies, next startup can resume via
--session-id. Staleness checked via file mtime (not embedded timestamp).
TTL: 4 hours (matches backend's BRIDGE_LAST_POLL_TTL).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

BRIDGE_POINTER_TTL_MS = 4 * 60 * 60 * 1000  # 4 hours

MAX_WORKTREE_FANOUT = 50


def get_bridge_pointer_path(
    directory: str, get_projects_dir: Any = None, sanitize_path_fn: Any = None
) -> str:
    """Get the bridge pointer file path for a directory."""
    if get_projects_dir and sanitize_path_fn:
        projects_dir = get_projects_dir()
        safe_dir = sanitize_path_fn(directory)
        return os.path.join(projects_dir, safe_dir, "bridge-pointer.json")
    return os.path.join(directory, ".claude", "bridge-pointer.json")


def write_bridge_pointer(
    directory: str,
    pointer: dict[str, Any],
    get_projects_dir: Any = None,
    sanitize_path_fn: Any = None,
) -> None:
    """Write or refresh the bridge pointer.

    Refreshes mtime during long sessions — same-content rewrite
    bumps the staleness clock. Best-effort: never cause a crash.
    """
    path = get_bridge_pointer_path(directory, get_projects_dir, sanitize_path_fn)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pointer, f)
    except OSError:
        pass


def read_bridge_pointer(
    directory: str, get_projects_dir: Any = None, sanitize_path_fn: Any = None
) -> Optional[dict[str, Any]]:
    """Read the bridge pointer and its age (ms since last write).

    Returns None on any failure: missing file, corrupted JSON, stale (>4h mtime).
    Stale/invalid pointers are deleted.
    """
    path = get_bridge_pointer_path(directory, get_projects_dir, sanitize_path_fn)

    try:
        mtime_ms = os.path.getmtime(path) * 1000
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        clear_bridge_pointer(directory, get_projects_dir, sanitize_path_fn)
        return None

    # Validate required fields
    if not isinstance(data, dict):
        return None
    if not data.get("sessionId") or not data.get("environmentId"):
        clear_bridge_pointer(directory, get_projects_dir, sanitize_path_fn)
        return None

    age_ms = max(0, time.time() * 1000 - mtime_ms)
    if age_ms > BRIDGE_POINTER_TTL_MS:
        clear_bridge_pointer(directory, get_projects_dir, sanitize_path_fn)
        return None

    return {**data, "ageMs": age_ms}


async def read_bridge_pointer_across_worktrees(
    directory: str,
    get_projects_dir: Any = None,
    sanitize_path_fn: Any = None,
    get_worktree_paths: Any = None,
) -> Optional[dict[str, Any]]:
    """Worktree-aware read for --continue.

    Fast path: checks directory first. Fans out across git worktree
    siblings to find the freshest pointer.
    """
    here = read_bridge_pointer(directory, get_projects_dir, sanitize_path_fn)
    if here:
        return {"pointer": here, "dir": directory}

    if not get_worktree_paths:
        return None

    try:
        worktrees = await get_worktree_paths(directory)
    except Exception:
        return None

    if len(worktrees) <= 1:
        return None
    if len(worktrees) > MAX_WORKTREE_FANOUT:
        return None

    # Fan out parallel reads
    dir_key = sanitize_path_fn(directory) if sanitize_path_fn else directory
    candidates = [
        wt
        for wt in worktrees
        if (sanitize_path_fn(wt) if sanitize_path_fn else wt) != dir_key
    ]

    freshest: Optional[dict[str, Any]] = None
    for wt in candidates:
        p = read_bridge_pointer(wt, get_projects_dir, sanitize_path_fn)
        if p and (
            freshest is None
            or p.get("ageMs", 0)
            < freshest.get("pointer", {}).get("ageMs", float("inf"))
        ):
            freshest = {"pointer": p, "dir": wt}

    return freshest


def clear_bridge_pointer(
    directory: str, get_projects_dir: Any = None, sanitize_path_fn: Any = None
) -> None:
    """Delete the pointer. Idempotent — ENOENT is expected."""
    path = get_bridge_pointer_path(directory, get_projects_dir, sanitize_path_fn)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass
