"""
/memory command - open and edit session memory files (HARE.md / CLAUDE.md).

Port of: src/commands/memory/memory.tsx + index.ts

In the TS CLI this opens a MemoryFileSelector dialog. In the headless SDK,
it opens the memory file in the configured editor ($EDITOR or $VISUAL).
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

COMMAND_NAME = "memory"
DESCRIPTION = "Open and edit memory files (CLAUDE.md / HARE.md)"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Open memory file for editing.

    Determines which memory file to open based on context,
    creates it if it doesn't exist, and opens in $EDITOR/$VISUAL.
    """
    get_claude_config_home_dir = context.get("get_claude_config_home_dir")
    get_memory_files = context.get("get_memory_files")
    get_original_cwd = context.get("get_original_cwd")

    # Determine the editor
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
    editor_source = (
        "$VISUAL"
        if os.environ.get("VISUAL")
        else ("$EDITOR" if os.environ.get("EDITOR") else "default")
    )

    # Determine memory file paths
    memory_files = []
    if get_memory_files:
        memory_files = await get_memory_files()

    if memory_files:
        # Open the first available memory file
        memory_path = (
            memory_files[0]
            if isinstance(memory_files[0], str)
            else memory_files[0].get("path", "")
        )
    else:
        # Default: look for CLAUDE.md or HARE.md in current directory
        cwd = get_original_cwd() if get_original_cwd else os.getcwd()
        for name in ["CLAUDE.md", "HARE.md", "claude.md", "hare.md"]:
            candidate = os.path.join(cwd, name)
            if os.path.exists(candidate):
                memory_path = candidate
                break
        else:
            memory_path = os.path.join(cwd, "CLAUDE.md")

    # Ensure the file exists
    os.makedirs(os.path.dirname(memory_path), exist_ok=True)
    if not os.path.exists(memory_path):
        with open(memory_path, "w", encoding="utf-8") as f:
            f.write("")

    # Open in editor
    try:
        subprocess.run([editor, memory_path], check=False)
    except FileNotFoundError:
        return {
            "type": "text",
            "value": f"Editor '{editor}' not found. Set $EDITOR or $VISUAL to change.\nMemory file path: {memory_path}",
            "display": "system",
        }

    # Build result message
    editor_info = (
        f'Using {editor_source}="{editor}".' if editor_source != "default" else ""
    )
    editor_hint = (
        f"> {editor_info} To change editor, set $EDITOR or $VISUAL environment variable."
        if editor_info
        else "> To use a different editor, set the $EDITOR or $VISUAL environment variable."
    )

    return {
        "type": "text",
        "value": f"Opened memory file at {memory_path}\n\n{editor_hint}",
        "display": "system",
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
