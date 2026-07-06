"""
Git filesystem helpers.

Port of: src/utils/git/gitFilesystem.ts
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Optional


async def get_head_for_dir(dir_path: str) -> Optional[str]:
    """Return full commit SHA for `dir_path` if it is a git work tree, else None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            dir_path,
            "rev-parse",
            "HEAD",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        sha = out.decode().strip()
        return sha if len(sha) >= 40 else None
    except (FileNotFoundError, OSError):
        return None
