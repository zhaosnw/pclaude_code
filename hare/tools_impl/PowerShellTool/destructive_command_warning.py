"""Warn on destructive PowerShell patterns. Port of: src/tools/PowerShellTool/destructiveCommandWarning.ts"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_DESTRUCTIVE = re.compile(
    r"\b(Remove-Item\s+-Recurse|Format-Volume|Clear-Disk|Stop-Computer|Restart-Computer)\b",
    re.IGNORECASE,
)

DESTRUCTIVE_PS_PATTERNS: list[tuple[str, str]] = [
    (r"Remove-Item\s+.*-Recurse", "Recursive file deletion may lose data permanently"),
    (r"\brm\s+-r\s+-fo\b", "Recursive force delete will bypass confirmation prompts"),
    (r"Remove-Item\s+.*\$env:(SystemRoot|WinDir|ProgramFiles)", "Attempting to delete system directory"),
    (r"Remove-Item\s+.*[CDFG]:\\\*", "Attempting to delete from root of a drive"),
    (r"Format-Volume\b", "Formatting a disk volume will destroy all data on it"),
    (r"\bformat\s+[A-Z]:", "Format command will wipe the target drive"),
    (r"Clear-Disk\b", "Clearing a disk will remove all partition data"),
    (r"Stop-Computer\b", "Shutting down the computer"),
    (r"Restart-Computer\b", "Restarting the computer without warning"),
    (r"Invoke-Expression\s+.*(Invoke-WebRequest|curl|wget)", "Executing downloaded content via Invoke-Expression is dangerous"),
    (r"\biex\s+.*(iwr|curl|wget)", "Executing downloaded content via iex is dangerous"),
    (r"Set-ExecutionPolicy\s+(Unrestricted|Bypass|RemoteSigned)", "Weakening PowerShell execution policy"),
    (r"Clear-RecycleBin\b", "Permanently deleting Recycle Bin contents"),
    (r"\brmdir\s+/s\s+/q\s+(C:|D:)\\", "Recursive force delete from drive root via cmd"),
    (r"\brd\s+/s\s+/q\s+(C:|D:)\\", "Recursive force delete from drive root via cmd"),
    (r"\bdiskpart\b", "Running diskpart may alter disk layout"),
    (r"Remove-WindowsFeature\b", "Removing Windows features may affect system stability"),
    (r"Disable-ComputerRestore\b", "Disabling system restore removes recovery points"),
    (r"Stop-Process\s+-Name\s+(explorer|winlogon|lsass|csrss|smss|services)", "Stopping a critical system process"),
    (r"Remove-ADUser\b", "Removing Active Directory user accounts"),
    (r"Remove-ADGroup\b", "Removing Active Directory group"),
    (r"\bgit\s+push\s+.*--force", "Force push may overwrite remote history"),
    (r"\bgit\s+reset\s+--hard", "Hard reset will discard uncommitted changes"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f", "Git clean will permanently delete untracked files"),
    (r"Remove-ItemProperty\s+.*HKLM:\\", "Modifying HKEY_LOCAL_MACHINE registry"),
    (r"Set-ItemProperty\s+.*HKLM:\\", "Modifying HKEY_LOCAL_MACHINE registry"),
]


def is_potentially_destructive_powershell(command: str) -> bool:
    """Return True if the command matches a broad destructive PowerShell pattern."""
    if _DESTRUCTIVE.search(command):
        return True
    for pattern, _warning in DESTRUCTIVE_PS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def get_destructive_powershell_warning(command: str) -> Optional[str]:
    """Check if a PowerShell command matches destructive patterns and return a warning message.

    Returns the first matching warning string, or None if no pattern matches.
    """
    cmd = command.strip()
    for pattern, warning in DESTRUCTIVE_PS_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return warning
    return None


@dataclass(frozen=True)
class DestructiveWarningResult:
    """Structured result from destructive command analysis."""
    is_destructive: bool
    warning: Optional[str] = None

    @classmethod
    def from_command(cls, command: str) -> DestructiveWarningResult:
        warning = get_destructive_powershell_warning(command)
        return cls(is_destructive=warning is not None, warning=warning)


def is_destructive_powershell(command: str) -> bool:
    """Convenience: return True when a destructive warning would be produced."""
    return get_destructive_powershell_warning(command) is not None
