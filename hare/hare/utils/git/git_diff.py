"""
Git diff computation.
Port of: src/utils/gitDiff.ts
"""

from __future__ import annotations
import asyncio


async def compute_git_diff(
    cwd: str | None = None,
    base_ref: str = "HEAD",
    target_ref: str | None = None,
    paths: list[str] | None = None,
    max_size: int = 100_000,
) -> str:
    """Compute git diff between two refs."""
    cmd = ["git", "--no-optional-locks", "diff"]
    if target_ref:
        cmd.append(f"{base_ref}...{target_ref}")
    else:
        cmd.append(base_ref)
    if paths:
        cmd.append("--")
        cmd.extend(paths)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace")
        if len(output) > max_size:
            output = output[:max_size] + "\n... (truncated)"
        return output
    except Exception:
        return ""


async def compute_diff_stat(cwd: str | None = None, base_ref: str = "HEAD") -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--no-optional-locks",
            "diff",
            "--stat",
            base_ref,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="replace").strip()
    except Exception:
        return ""
