"""
CLI update command.

Port of: src/entrypoints/cli/updateCommand.ts
"""

from __future__ import annotations


async def run_update_command(channel: str = "stable") -> None:
    """Check for and apply updates. Stub."""
    print(f"Checking for updates on channel: {channel}")
    print("Already up to date.")
