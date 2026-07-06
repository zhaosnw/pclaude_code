"""
Git worktree paths with analytics.

Port of: src/utils/getWorktreePaths.ts
"""

from __future__ import annotations

import os
import time

from hare.utils.exec_file_no_throw import exec_file_no_throw_with_cwd


def _log_event(*_a, **_k):
    return None


def set_worktree_analytics_log_event(fn: object) -> None:
    global _log_event
    _log_event = fn


async def get_worktree_paths(cwd: str) -> list[str]:
    start = time.time() * 1000
    r = await exec_file_no_throw_with_cwd(
        "git",
        ["worktree", "list", "--porcelain"],
        cwd=cwd,
        preserve_output_on_error=False,
    )
    duration_ms = time.time() * 1000 - start
    if r["code"] != 0:
        _log_event(
            "tengu_worktree_detection",
            {"duration_ms": duration_ms, "worktree_count": 0, "success": False},
        )
        return []
    lines = (r["stdout"] or "").split("\n")
    paths = [
        line[len("worktree ") :].strip()
        for line in lines
        if line.startswith("worktree ")
    ]
    paths = [os.path.normpath(p) for p in paths]
    _log_event(
        "tengu_worktree_detection",
        {"duration_ms": duration_ms, "worktree_count": len(paths), "success": True},
    )
    sep = os.sep
    current = next((p for p in paths if cwd == p or cwd.startswith(p + sep)), None)
    others = sorted([p for p in paths if p != current])
    return [current, *others] if current else others
