"""Port of: src/utils/bash/bashPipeCommand.ts"""

from __future__ import annotations
import re


def split_pipe_commands(command: str) -> list[str]:
    parts = re.split(r"\s*\|\s*", command)
    return [p.strip() for p in parts if p.strip()]


def has_pipe(command: str) -> bool:
    return "|" in command
