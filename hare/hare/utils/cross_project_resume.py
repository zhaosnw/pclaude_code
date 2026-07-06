"""Cross-directory resume hints (`crossProjectResume.ts`)."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class CrossProjectNotCross:
    is_cross_project: Literal[False] = False


@dataclass
class CrossProjectSameWorktree:
    is_cross_project: Literal[True]
    is_same_repo_worktree: Literal[True]
    project_path: str


@dataclass
class CrossProjectDiff:
    is_cross_project: Literal[True]
    is_same_repo_worktree: Literal[False]
    command: str
    project_path: str


CrossProjectResumeResult = (
    CrossProjectNotCross | CrossProjectSameWorktree | CrossProjectDiff
)


def check_cross_project_resume(
    log: Any,
    show_all_projects: bool,
    worktree_paths: list[str],
    *,
    original_cwd: str,
    get_session_id_from_log: Any,
) -> CrossProjectResumeResult:
    current_cwd = original_cwd
    project_path = getattr(log, "project_path", None)
    if not show_all_projects or not project_path or project_path == current_cwd:
        return CrossProjectNotCross()

    if os.environ.get("USER_TYPE") != "ant":
        sid = get_session_id_from_log(log)
        cmd = f"cd {shlex.quote(project_path)} && hare --resume {sid}"
        return CrossProjectDiff(True, False, cmd, project_path)

    import os.path as osp

    sep = osp.sep
    is_same = any(
        project_path == wt or project_path.startswith(wt + sep) for wt in worktree_paths
    )
    if is_same:
        return CrossProjectSameWorktree(True, True, project_path)

    sid = get_session_id_from_log(log)
    cmd = f"cd {shlex.quote(project_path)} && hare --resume {sid}"
    return CrossProjectDiff(True, False, cmd, project_path)
