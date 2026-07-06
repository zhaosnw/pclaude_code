"""
Permission helpers for piped/compound bash commands.

Port of: src/tools/BashTool/bashCommandHelpers.ts
"""

from __future__ import annotations

import re
from typing import Any, Callable, Awaitable

from hare.utils.bash.commands import split_command


CommandIdentityCheckers = dict  # {"is_normalized_cd_command": Callable, "is_normalized_git_command": Callable}


def _is_cd_command(cmd: str) -> bool:
    return bool(re.match(r"^cd(\s|$)", cmd.strip()))


def _is_git_command(cmd: str) -> bool:
    return bool(re.match(r"^git(\s|$)", cmd.strip()))


DEFAULT_CHECKERS: CommandIdentityCheckers = {
    "is_normalized_cd_command": _is_cd_command,
    "is_normalized_git_command": _is_git_command,
}


async def segmented_command_permission_result(
    input_args: dict[str, Any],
    segments: list[str],
    has_permission_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    checkers: CommandIdentityCheckers | None = None,
) -> dict[str, Any]:
    """Check permissions for each pipe segment of a compound command."""
    if checkers is None:
        checkers = DEFAULT_CHECKERS

    is_cd = checkers.get("is_normalized_cd_command", _is_cd_command)
    is_git = checkers.get("is_normalized_git_command", _is_git_command)

    cd_commands = [s for s in segments if is_cd(s.strip())]
    if len(cd_commands) > 1:
        return {
            "behavior": "ask",
            "message": "Multiple directory changes in one command require approval for clarity",
        }

    has_cd = False
    has_git = False
    for segment in segments:
        subcommands = split_command(segment)
        for sub in subcommands:
            trimmed = sub.strip()
            if is_cd(trimmed):
                has_cd = True
            if is_git(trimmed):
                has_git = True

    if has_cd and has_git:
        return {
            "behavior": "ask",
            "message": "Compound commands with cd and git require approval to prevent bare repository attacks",
        }

    segment_results: dict[str, dict[str, Any]] = {}
    for segment in segments:
        trimmed = segment.strip()
        if not trimmed:
            continue
        result = await has_permission_fn({**input_args, "command": trimmed})
        segment_results[trimmed] = result

    denied = next(
        ((cmd, r) for cmd, r in segment_results.items() if r.get("behavior") == "deny"),
        None,
    )
    if denied:
        cmd, result = denied
        return {
            "behavior": "deny",
            "message": result.get("message", f"Permission denied for: {cmd}"),
        }

    all_allowed = all(r.get("behavior") == "allow" for r in segment_results.values())
    if all_allowed:
        return {"behavior": "allow", "updatedInput": input_args}

    return {"behavior": "ask", "message": "Some pipe segments require approval"}


async def check_command_operator_permissions(
    input_args: dict[str, Any],
    has_permission_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    checkers: CommandIdentityCheckers | None = None,
) -> dict[str, Any]:
    """Check for special operators (pipes, subshells) in a command."""
    command = input_args.get("command", "")

    if "|" not in command:
        return {"behavior": "passthrough", "message": "No pipes found in command"}

    segments = [s.strip() for s in command.split("|")]
    if len(segments) <= 1:
        return {"behavior": "passthrough", "message": "No pipes found in command"}

    return await segmented_command_permission_result(
        input_args, segments, has_permission_fn, checkers
    )
