"""Port of: src/commands/worktree.ts. Show and manage git worktrees."""

from __future__ import annotations
import asyncio
from typing import Any

COMMAND_NAME = "worktree"
DESCRIPTION = "Manage git worktrees"
ALIASES: list[str] = ["wt"]

HELP = """worktree [list|add <path> [<ref>]|remove <path>|prune]

Subcommands:
  list              List all worktrees (default).
  add <path> [ref]  Create a new worktree at <path> from <ref> (default: HEAD).
  remove <path>     Remove a worktree at <path>.
  prune             Prune worktree metadata for removed directories."""


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show git worktree info or run a worktree subcommand.

    When no subcommand is given, runs `git worktree list` to display all
    active worktrees with their branches and hashes. Supports add, remove,
    and prune subcommands to manage worktrees.
    """
    subcmd = args[0] if args else "list"
    if subcmd in ("-h", "--help"):
        return {"type": "text", "value": HELP}

    valid_subcmds = {"list", "add", "remove", "prune"}
    if subcmd not in valid_subcmds:
        return {
            "type": "text",
            "value": f"Unknown subcommand: {subcmd}\n\n{HELP}",
        }

    cmd_parts = ["git", "worktree", subcmd, *args[1:]]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {
                "type": "text",
                "value": stderr.decode("utf-8", errors="replace").strip(),
            }
        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            output = "(no worktrees)"
        return {"type": "text", "value": output}
    except asyncio.TimeoutError:
        return {"type": "text", "value": "Worktree command timed out."}
    except FileNotFoundError:
        return {
            "type": "text",
            "value": "git not found. Is git installed and on PATH?",
        }
    except Exception as exc:
        return {
            "type": "text",
            "value": f"Worktree command failed: {exc}",
        }
