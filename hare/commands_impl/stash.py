"""Port of: src/commands/stash.ts. Manage git stash operations (list, push, pop, apply, drop)."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

COMMAND_NAME = "stash"
DESCRIPTION = "Stash or unstash git changes (list/push/pop/apply/drop)"
ALIASES: list[str] = ["snapshot", "save"]

STASH_HELP = """Usage: /stash [list|push|pop|apply|drop] [args...]

Commands:
  list             List all stash entries
  push [message]   Stash working directory changes (push a new stash)
  pop  [index]     Apply and remove the most recent (or specified) stash
  apply [index]    Apply but keep the stash entry
  drop [index]     Remove the most recent (or specified) stash entry

If no subcommand is given, "list" is the default."""


def _build_git_cmd(subcommand: str, extra_args: list[str]) -> list[str]:
    """Build a safe git stash command."""
    return ["git", "stash", subcommand] + extra_args


async def _run_git(cmd: list[str]) -> str:
    """Run a git command and return its stdout (or stderr) as a string."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"git stash failed with exit code {proc.returncode}")
    return stdout.decode("utf-8", errors="replace").strip()


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Run git stash operations: list (default), push, pop, apply, or drop."""
    if not args:
        try:
            output = await _run_git(["git", "stash", "list"])
            return {
                "type": "text",
                "value": output or "No stash entries.",
            }
        except RuntimeError as exc:
            return {"type": "text", "value": f"Error: {exc}"}

    subcommand = args[0].lower()
    extra = args[1:]

    # --help / help flag
    if subcommand in ("--help", "-h", "help"):
        return {"type": "text", "value": STASH_HELP}

    # Validate subcommand
    if subcommand not in ("list", "push", "pop", "apply", "drop", "save", "show"):
        return {
            "type": "text",
            "value": STASH_HELP
            + f"\n\nUnknown subcommand: '{subcommand}'",
        }

    # Normalize aliases
    if subcommand == "save":
        subcommand = "push"

    try:
        output = await _run_git(_build_git_cmd(subcommand, extra))
    except RuntimeError as exc:
        return {"type": "text", "value": f"git stash {subcommand} failed:\n{exc}"}

    # Provide human-friendly summaries
    if subcommand == "list":
        entries = output.strip()
        return {
            "type": "text",
            "value": entries if entries else "No stash entries.",
        }
    elif subcommand == "push":
        msg = ""
        if extra:
            msg = f" (\"{' '.join(extra)}\")"
        return {
            "type": "text",
            "value": output.strip()
            or f"Working directory changes stashed{msg}.",
        }
    elif subcommand == "pop":
        return {
            "type": "text",
            "value": output.strip() or "Stash popped and applied successfully.",
        }
    elif subcommand == "apply":
        return {
            "type": "text",
            "value": output.strip()
            or "Stash applied. Use /stash drop to remove it from the stash list.",
        }
    elif subcommand == "drop":
        return {
            "type": "text",
            "value": output.strip() or "Stash entry dropped.",
        }
    elif subcommand == "show":
        return {"type": "text", "value": output or ""}

    return {"type": "text", "value": output or "Done."}
