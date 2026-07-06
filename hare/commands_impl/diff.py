"""Port of: src/commands/diff/. Show git diff of uncommitted changes."""

from __future__ import annotations
from typing import Any
import asyncio

COMMAND_NAME = "diff"
DESCRIPTION = "Show git diff of uncommitted changes (working tree vs index, or staged changes with --staged)"
ALIASES: list[str] = []


def _build_cmd(args: list[str]) -> list[str]:
    """Build the 'git diff' command line from user args."""
    base: list[str] = ["git", "diff", "--unified=3"]
    extra: set[str] = set()

    for a in args:
        a_stripped = a.strip()
        if a_stripped in ("--staged", "--cached"):
            extra.add("--cached")
        elif a_stripped == "--stat":
            extra.add("--stat")
        elif a_stripped == "--name-only":
            extra.add("--name-only")
        elif a_stripped == "--name-status":
            extra.add("--name-status")
        elif a_stripped.startswith("--"):
            extra.add(a_stripped)        # pass through unknown flags
        else:
            # positional: file/directory path
            extra.add(a_stripped)

    # Avoid mixing incompatible flags: only allow one output format.
    format_flags = {"--stat", "--name-only", "--name-status"}
    chosen = extra & format_flags
    if len(chosen) > 1:
        # If multiple format flags are given, prefer --stat.
        extra -= chosen
        extra.add("--stat")
    elif not chosen:
        # Default to unified diff.
        pass  # --unified=3 already set

    # --cached is compatible with all formats; always place it after 'diff'.
    result = ["git", "diff"]
    if "--cached" in extra:
        result.append("--cached")
        extra.discard("--cached")
    result.append("--unified=3")
    result.extend(sorted(extra))
    return result


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Run git diff with optional flags (--staged, --stat, --name-only, etc.) and return the output."""
    try:
        cmd = _build_cmd(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out_text = stdout.decode(errors="replace")
        err_text = stderr.decode(errors="replace")

        if proc.returncode != 0 and err_text:
            return {
                "type": "error",
                "subtype": "git_error",
                "value": err_text.strip(),
                "return_code": proc.returncode,
            }

        value = out_text.strip() or "(no changes)"
        return {"type": "text", "value": value}
    except FileNotFoundError:
        return {
            "type": "error",
            "subtype": "missing_git",
            "value": "git is not installed or not on PATH.",
        }
    except Exception as e:
        return {"type": "error", "subtype": "exception", "value": f"{type(e).__name__}: {e}"}
