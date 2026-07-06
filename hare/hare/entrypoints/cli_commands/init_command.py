"""
CLI init command – initialize a new project.

Port of: src/entrypoints/cli/initCommand.ts
"""

from __future__ import annotations

import os
from typing import Any


async def run_init_command(args: dict[str, Any] | None = None) -> None:
    """Initialize Hare in the current project."""
    cwd = os.getcwd()
    hare_dir = os.path.join(cwd, ".hare")
    os.makedirs(hare_dir, exist_ok=True)
    memory_file = os.path.join(cwd, "HARE.md")
    if not os.path.exists(memory_file):
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write("# Project Memory\n\n")
    print(f"Initialized Hare in {cwd}")
