"""Port of: src/utils/bash/shellCompletion.ts"""

from __future__ import annotations
import os
import glob as _glob


def get_path_completions(prefix: str, cwd: str = "") -> list[str]:
    base = cwd or os.getcwd()
    pattern = os.path.join(base, prefix + "*")
    return sorted(_glob.glob(pattern))[:50]


def get_command_completions(prefix: str) -> list[str]:
    if not prefix:
        return []
    common = [
        "ls",
        "cd",
        "cat",
        "git",
        "python",
        "pip",
        "npm",
        "node",
        "docker",
        "grep",
        "find",
        "echo",
        "mkdir",
        "rm",
        "cp",
        "mv",
    ]
    return [c for c in common if c.startswith(prefix)]
