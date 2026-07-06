"""
Portable git worktree listing (stdlib only).

Port of: src/utils/getWorktreePathsPortable.ts
"""

from __future__ import annotations

import asyncio
import os


async def get_worktree_paths_portable(cwd: str) -> list[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "list",
            "--porcelain",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return []
        if proc.returncode != 0 or not stdout:
            return []
        text = stdout.decode("utf-8", errors="replace")
        paths = [
            line[len("worktree ") :].strip()
            for line in text.split("\n")
            if line.startswith("worktree ")
        ]
        return [os.path.normpath(p) for p in paths]
    except (FileNotFoundError, OSError):
        return []
