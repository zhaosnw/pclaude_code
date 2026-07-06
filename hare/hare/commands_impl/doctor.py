"""
/doctor command - diagnose system issues.

Port of: src/commands/doctor/doctor.tsx + index.ts

Provides system diagnostics: Python version, platform, working directory,
home directory, session info, and configuration paths.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any

COMMAND_NAME = "doctor"
DESCRIPTION = "Diagnose system and environment issues"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Run system diagnostics and return a report."""
    get_session_id = context.get("get_session_id")
    get_original_cwd = context.get("get_original_cwd")

    session_id = get_session_id() if get_session_id else "N/A"
    original_cwd = get_original_cwd() if get_original_cwd else os.getcwd()

    lines = [
        "## System Diagnostics",
        "",
        f"**Python:** {sys.version}",
        f"**Platform:** {sys.platform} ({platform.machine()})",
        f"**OS:** {platform.system()} {platform.release()}",
        f"**CWD:** {os.getcwd()}",
        f"**Original CWD:** {original_cwd}",
        f"**HOME:** {os.path.expanduser('~')}",
        f"**Session ID:** {session_id}",
        f"**Shell:** {os.environ.get('SHELL', 'unknown')}",
        f"**Terminal:** {os.environ.get('TERM', 'unknown')}",
    ]

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
