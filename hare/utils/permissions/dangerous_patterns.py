"""Regex patterns for risky shell commands. Port of dangerousPatterns.ts."""

from __future__ import annotations

import re

SUDO_PATTERN = re.compile(r"\bsudo\b")
RM_RF_PATTERN = re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-?r[rf]?\b")
PIPE_TO_SHELL_PATTERN = re.compile(r"\|\s*(bash|sh|zsh|fish)\b")


def looks_dangerous_command(cmd: str) -> bool:
    c = cmd.strip()
    return bool(SUDO_PATTERN.search(c) or RM_RF_PATTERN.search(c))
