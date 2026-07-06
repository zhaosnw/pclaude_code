"""Team memory paths and validation (port of src/memdir/teamMemPaths.ts)."""

from __future__ import annotations

import os
from pathlib import Path

from hare.memdir.paths import get_auto_mem_path, is_auto_memory_enabled


class PathTraversalError(Exception):
    pass


def is_team_memory_enabled() -> bool:
    return is_auto_memory_enabled() and os.environ.get(
        "TENGU_HERRING_CLOCK", ""
    ).lower() in (
        "1",
        "true",
    )


def get_team_mem_path() -> str:
    return (Path(get_auto_mem_path()) / "team").as_posix() + "/"


def get_team_mem_entrypoint() -> str:
    return str(Path(get_auto_mem_path()) / "team" / "MEMORY.md")


def is_team_mem_path(file_path: str) -> bool:
    resolved = Path(file_path).resolve()
    team = Path(get_team_mem_path().rstrip("/\\"))
    try:
        resolved.relative_to(team)
    except ValueError:
        return False
    return True


async def validate_team_mem_write_path(file_path: str) -> str:
    if "\0" in file_path:
        raise PathTraversalError("Null byte in path")
    resolved = str(Path(file_path).resolve())
    if not resolved.startswith(get_team_mem_path().replace("/", os.sep).rstrip("\\")):
        raise PathTraversalError("Path escapes team memory directory")
    return resolved


async def validate_team_mem_key(relative_key: str) -> str:
    if "\\" in relative_key or relative_key.startswith("/"):
        raise PathTraversalError("Invalid key")
    full = Path(get_team_mem_path()) / relative_key
    return await validate_team_mem_write_path(str(full))


def is_team_mem_file(file_path: str) -> bool:
    return is_team_memory_enabled() and is_team_mem_path(file_path)
