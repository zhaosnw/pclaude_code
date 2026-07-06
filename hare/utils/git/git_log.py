"""Git log helpers."""

from __future__ import annotations

import asyncio


async def get_recent_commits(n: int = 10, cwd: str | None = None) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--no-optional-locks",
            "log",
            "--oneline",
            "-n",
            str(n),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="replace").strip()
    except Exception:
        return ""
