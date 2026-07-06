"""Common PowerShell parameter name sets. Port of: src/tools/PowerShellTool/commonParameters.ts"""

from __future__ import annotations

COMMON_POWERSHELL_PARAMETERS = frozenset(
    {
        "Verbose",
        "Debug",
        "ErrorAction",
        "WarningAction",
        "InformationAction",
        "OutVariable",
    }
)
