"""
Backend environment detection.

Port of: src/utils/swarm/backends/detection.ts
"""

from __future__ import annotations

import os
import shutil


def is_inside_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


is_inside_tmux_sync = is_inside_tmux


def is_in_iterm2() -> bool:
    return os.environ.get("TERM_PROGRAM") == "iTerm.app"


def is_tmux_available() -> bool:
    return shutil.which("tmux") is not None


async def is_it2_cli_available() -> bool:
    return shutil.which("it2") is not None
