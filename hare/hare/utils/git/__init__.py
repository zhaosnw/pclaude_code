"""Git utilities."""

from __future__ import annotations

from typing import Optional

from hare.utils.git.git_diff import compute_git_diff
from hare.utils.git.git_status import get_git_status


async def find_git_root(cwd: str = "") -> Optional[str]:
    """Find the git root directory."""
    import asyncio
    import os

    search = cwd or os.getcwd()
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--show-toplevel",
            cwd=search,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception:
        pass
    return None


async def is_git_repo(cwd: str = "") -> bool:
    """Check if a directory is inside a git repository."""
    root = await find_git_root(cwd)
    return root is not None


async def get_current_branch(cwd: str = "") -> Optional[str]:
    """Get the current git branch name."""
    import asyncio

    search = cwd or ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            cwd=search or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception:
        pass
    return None
