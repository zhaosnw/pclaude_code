"""
HARE.md / memory loading (`hare_md.ts`).

Full discovery, marked lexer, and hooks are deferred; exported constants and
types match the TypeScript public surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

MemoryType = Literal["User", "Project", "Local", "Managed", "AutoMem", "TeamMem"]

MAX_MEMORY_CHARACTER_COUNT = 40_000

MEMORY_INSTRUCTION_PROMPT = (
    "Codebase and user instructions are shown below. Be sure to adhere to these instructions. "
    "IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written."
)


@dataclass
class MemoryFileInfo:
    path: str
    type: MemoryType
    content: str
    parent: str | None = None
    globs: list[str] | None = None
    content_differs_from_disk: bool | None = None
    raw_content: str | None = None


async def get_memory_files(
    _force_include_external: bool = False,
) -> list[MemoryFileInfo]:
    return []


def clear_memory_file_caches() -> None:
    """Invalidate memoized memory load when wiring `cache` + hooks."""
    pass


def get_large_memory_files(files: list[MemoryFileInfo]) -> list[MemoryFileInfo]:
    return [f for f in files if len(f.content) > MAX_MEMORY_CHARACTER_COUNT]


def get_hare_mds(
    memory_files: list[MemoryFileInfo],
    filter_fn: Callable[[MemoryType], bool] | None = None,
) -> str:
    memories: list[str] = []
    for file in memory_files:
        if filter_fn and not filter_fn(file.type):
            continue
        if file.content.strip():
            memories.append(f"Contents of {file.path}:\n\n{file.content.strip()}")
    if not memories:
        return ""
    return f"{MEMORY_INSTRUCTION_PROMPT}\n\n" + "\n\n".join(memories)
