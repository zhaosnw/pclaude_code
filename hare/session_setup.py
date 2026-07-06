"""
Session setup – initialize project environment before starting REPL.

Port of: src/setup.ts

Named ``session_setup.py`` (not ``setup.py``) so ``pip install -e .`` does not
execute this file as a setuptools build script.
"""

from __future__ import annotations

import os
from typing import Any

from hare.bootstrap.state import (
    get_session_id,
    set_original_cwd,
    set_project_root,
)
from hare.utils.config import get_global_config
from hare.utils.git import find_git_root


async def setup(
    cwd: str,
    permission_mode: str = "default",
    allow_dangerously_skip_permissions: bool = False,
    worktree_enabled: bool = False,
) -> dict[str, Any]:
    """
    Initialize project environment.

    Returns setup result with project root, git status, session info.
    """
    set_original_cwd(cwd)
    os.chdir(cwd)

    git_root = await find_git_root(cwd)
    project_root = git_root or cwd
    set_project_root(project_root)

    session_id = get_session_id()
    config = get_global_config()

    result: dict[str, Any] = {
        "cwd": cwd,
        "project_root": project_root,
        "git_root": git_root,
        "session_id": session_id,
        "permission_mode": permission_mode,
        "worktree_enabled": worktree_enabled,
    }

    if worktree_enabled and git_root:
        result["worktree"] = {"enabled": True, "branch": None}

    return result
