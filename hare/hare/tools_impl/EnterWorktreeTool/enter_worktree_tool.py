"""
EnterWorktreeTool – create and switch to a git worktree for isolated work.

Port of: src/tools/EnterWorktreeTool/EnterWorktreeTool.ts

Creates a new git worktree on a new branch, providing an isolated
workspace for parallel development, testing, or subagent work.
"""

from __future__ import annotations
import asyncio
import os
import secrets
from pathlib import Path
from typing import Any

TOOL_NAME = "EnterWorktree"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Optional name for the worktree (auto-generated if omitted)",
            },
            "base_ref": {
                "type": "string",
                "description": "Base branch or commit for the worktree (default: current HEAD)",
            },
            "path": {
                "type": "string",
                "description": "Path to an existing worktree to enter instead of creating one",
            },
        },
    }


def is_destructive(input: dict[str, Any]) -> bool:
    return False


def is_concurrency_safe(input: dict[str, Any]) -> bool:
    return False


async def call(
    name: str | None = None,
    base_ref: str | None = None,
    path: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create or enter a git worktree.

    If 'path' is provided, enters an existing worktree.
    Otherwise, creates a new worktree with a new branch.
    """
    cwd = os.getcwd()

    # Verify git is available
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            return {"error": "Git is not available.", "data": ""}
    except FileNotFoundError:
        return {"error": "Git is not installed.", "data": ""}

    # Find the git root
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--show-toplevel",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"error": "Not in a git repository.", "data": ""}
        git_root = stdout.decode(errors="replace").strip()
    except Exception as e:
        return {"error": str(e), "data": ""}

    # Enter existing worktree
    if path:
        target = Path(path).resolve()
        if not target.exists():
            return {"error": f"Worktree path does not exist: {path}", "data": ""}

        # Verify it's a valid worktree
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "list",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            worktrees = stdout.decode(errors="replace")
            if str(target) not in worktrees:
                return {
                    "error": f"Path {path} is not a registered git worktree.",
                    "data": "",
                }
        except Exception:
            pass

        return {
            "data": f"Entered existing worktree at {target}",
            "path": str(target),
            "action": "enter_existing",
        }

    # Create new worktree
    if not name:
        name = f"hare-worktree-{secrets.token_hex(4)}"

    # Determine base ref
    if not base_ref:
        # Default to current branch
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, _ = await proc.communicate()
            base = stdout.decode(errors="replace").strip()
            if not base:
                # Detached HEAD — use HEAD
                base = "HEAD"
        except Exception:
            base = "HEAD"
    else:
        base = base_ref

    # Ensure unique branch name
    branch_name = name
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "--list", branch_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if stdout.decode(errors="replace").strip():
            branch_name = f"{name}-{secrets.token_hex(3)}"
    except Exception:
        pass

    # Determine worktree path
    worktree_dir = Path(git_root).parent / f".worktrees/{branch_name}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "-b", branch_name,
            str(worktree_dir), base,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=git_root,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return {"error": f"Failed to create worktree: {err}", "data": ""}

        output = stdout.decode(errors="replace").strip()
        return {
            "data": f"Created worktree at {worktree_dir} (branch: {branch_name})",
            "path": str(worktree_dir),
            "branch": branch_name,
            "base_ref": base,
            "action": "create_new",
            "git_root": git_root,
            "output": output,
        }
    except Exception as e:
        return {"error": str(e), "data": ""}
