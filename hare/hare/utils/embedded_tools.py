"""
Embedded bfs/ugrep search tools (native build).

Port of: src/utils/embeddedTools.ts
"""

from __future__ import annotations

import os
import sys

from hare.utils.env_utils import is_env_truthy


def has_embedded_search_tools() -> bool:
    if not is_env_truthy(os.environ.get("EMBEDDED_SEARCH_TOOLS")):
        return False
    e = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    return e not in ("sdk-ts", "sdk-py", "sdk-cli", "local-agent")


def embedded_search_tools_binary_path() -> str:
    return sys.executable
