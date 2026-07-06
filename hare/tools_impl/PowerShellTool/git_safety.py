"""Git-destructive pattern checks for PowerShell. Port of: src/tools/PowerShellTool/gitSafety.ts"""

from __future__ import annotations

import re

_GIT_RISKY = re.compile(
    r"\bgit\s+(push\s+--force|reset\s+--hard|clean\s+-fd)\b", re.IGNORECASE
)


def is_risky_git_powershell(command: str) -> bool:
    return bool(_GIT_RISKY.search(command))
