"""
Git status and context gathering.

Port of: src/context.ts getGitStatus
"""

from __future__ import annotations

import asyncio

MAX_STATUS_CHARS = 2000


async def _exec_git(*args: str, cwd: str | None = None) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--no-optional-locks",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="replace").strip()
    except Exception:
        return ""


async def get_is_git(cwd: str | None = None) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--is-inside-work-tree",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip().lower() == "true"
    except Exception:
        return False


async def get_git_status(cwd: str | None = None) -> str | None:
    if not await get_is_git(cwd):
        return None

    try:
        branch, status, log, user_name = await asyncio.gather(
            _exec_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd),
            _exec_git("status", "--short", cwd=cwd),
            _exec_git("log", "--oneline", "-n", "5", cwd=cwd),
            _exec_git("config", "user.name", cwd=cwd),
        )

        if len(status) > MAX_STATUS_CHARS:
            status = status[:MAX_STATUS_CHARS] + "\n... (truncated)"

        parts = [
            "Git status snapshot at conversation start.",
            f"Current branch: {branch}",
        ]
        if user_name:
            parts.append(f"Git user: {user_name}")
        parts.append(f"Status:\n{status or '(clean)'}")
        parts.append(f"Recent commits:\n{log}")
        return "\n\n".join(parts)
    except Exception:
        return None
