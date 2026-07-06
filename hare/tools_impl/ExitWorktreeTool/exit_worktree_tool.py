"""
ExitWorktreeTool – leave a git worktree and return to original directory.

Port of: src/tools/ExitWorktreeTool/ExitWorktreeTool.ts
"""

from __future__ import annotations
import asyncio
import os
from typing import Any

TOOL_NAME = "ExitWorktree"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["keep", "remove"],
                "description": "What to do with the worktree: keep it on disk or remove it",
            },
            "discard_changes": {
                "type": "boolean",
                "description": "Force removal even if there are uncommitted changes (action=remove only)",
            },
        },
        "required": ["action"],
    }


def is_destructive(input: dict[str, Any]) -> bool:
    return input.get("action") == "remove"


def is_concurrency_safe(input: dict[str, Any]) -> bool:
    return False


async def call(action: str = "keep", discard_changes: bool = False, **kwargs: Any) -> dict[str, Any]:
    """Exit a git worktree.

    When action is 'keep', simply confirms the worktree remains.
    When action is 'remove', removes the worktree directory and its branch.
    """
    cwd = os.getcwd()

    # Verify we're in a git worktree
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--git-dir",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"error": "Not in a git repository.", "data": ""}
    except FileNotFoundError:
        return {"error": "Git is not installed.", "data": ""}

    # Check if this is actually a worktree
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--show-toplevel",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await proc.communicate()
        worktree_root = stdout.decode(errors="replace").strip()
    except Exception:
        return {"error": "Could not determine worktree root.", "data": ""}

    if action == "keep":
        return {
            "data": f"Worktree kept at {cwd}.",
            "worktree_path": cwd,
            "action": "keep",
        }

    # action == "remove"
    if not discard_changes:
        # Check for uncommitted changes
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, _ = await proc.communicate()
            if stdout.decode(errors="replace").strip():
                return {
                    "error": (
                        "Worktree has uncommitted changes. "
                        "Commit them first or use discard_changes=true to force removal."
                    ),
                    "data": "",
                }
        except Exception:
            pass

    # Remove the worktree
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", worktree_root,
            *(["--force"] if discard_changes else []),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return {
                "error": f"Failed to remove worktree: {err}",
                "data": "",
            }
        output = stdout.decode(errors="replace").strip()
        return {
            "data": f"Worktree removed: {worktree_root}",
            "worktree_path": worktree_root,
            "action": "remove",
            "output": output,
        }
    except Exception as e:
        return {"error": str(e), "data": ""}
