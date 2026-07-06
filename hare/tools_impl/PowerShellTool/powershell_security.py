"""Security classification for PowerShell commands. Port of: src/tools/PowerShellTool/powershellSecurity.ts"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["safe", "low", "medium", "high", "critical"]

# ---------------------------------------------------------------------------
# Critical patterns — irreversible destruction, AMSI bypass, system tampering
# ---------------------------------------------------------------------------

_CRITICAL_RE = re.compile(
    r"\b(Clear-Disk|Format-Volume|Initialize-Disk|"
    r"(Stop|Restart)-Computer\s+-Force|"
    r"Remove-Item\s+.*-Recurse\s+.*-Force\s+[A-Z]:\\|"
    r"Remove-Item\s+.*HKLM:|"
    r"Set-MpPreference\s+-DisableRealtimeMonitoring\s+\$true|"
    r"Set-NetFirewallProfile\s+-Enabled\s+False|"
    r"\[System\.Reflection\.Assembly\]::Load\("
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# High risk patterns — eval-like, force-delete, policy bypass, elevation
# ---------------------------------------------------------------------------

_HIGH_RE = re.compile(
    r"\b(Invoke-Expression|iex|Invoke-Command|icm)\b|"
    r"\bRemove-Item\s+.*(-Recurse\s+-Force|-Force)|"
    r"\bSet-ExecutionPolicy\s+.*(Bypass|Unrestricted)|"
    r"\bStart-Process\s+.*-Verb\s+RunAs|"
    r"\b(New-LocalUser|Remove-LocalUser|Add-LocalGroupMember\s+.*Administrators)|"
    r"\bSet-Service\s+.*-Status\s+Stopped|"
    r"\b(Register-ScheduledTask|New-ScheduledTaskAction)|"
    r"\b(sc|Set-Content|Out-File)\s+.*[A-Z]:\\(Windows|Program\s*Files|System32)|"
    r"-ExecutionPolicy\s+(Bypass|Unrestricted)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Medium risk patterns — writes, network calls, process termination
# ---------------------------------------------------------------------------

_MEDIUM_RE = re.compile(
    r"\bRemove-Item\b|"
    r"\b(Stop-Process|spps|kill)\b|"
    r"\b(Set-Content|Out-File)\b|"
    r"\b(Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b|"
    r"\bStart-Process\b|"
    r"\b(Restart-Service|Move-Item|Rename-Item|Set-ItemProperty)\b|"
    r"\bCopy-Item\s+.*-Recurse\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Low-risk / safe commands — read-only and diagnostic cmdlets
# ---------------------------------------------------------------------------

_LOW_RISK_COMMANDS: frozenset[str] = frozenset({
    "Get-ChildItem", "gci", "dir", "ls",
    "Get-Content", "gc", "cat", "type",
    "Get-Location", "gl", "pwd",
    "Get-Process", "gps", "ps",
    "Get-Service", "gsv",
    "Get-Date", "date",
    "Write-Output", "echo", "write",
    "Write-Host",
    "Select-Object", "select",
    "Where-Object", "where", "?",
    "ForEach-Object", "foreach", "%",
    "Sort-Object", "sort",
    "Group-Object", "group",
    "Measure-Object", "measure",
    "Compare-Object", "compare", "diff",
    "Format-List", "fl",
    "Format-Table", "ft",
    "Get-Member", "gm",
    "Get-Command", "gcm",
    "Get-Help", "help", "man",
    "Get-Variable", "gv",
    "Get-Item", "gi",
    "Resolve-Path", "rvpa",
    "Split-Path", "Join-Path", "Test-Path",
    "ConvertTo-Json", "ConvertFrom-Json",
    "Tee-Object", "tee",
    "Out-Null",
})


def _strip_ps_comments_and_strings(command: str) -> str:
    """Remove PowerShell comment blocks, here-strings, and quoted strings."""
    # Here-strings: @' ... '@ and @" ... "@
    cmd = re.sub(r"@'[^@]*'@|@\"[^@]*\"@", " ", command, flags=re.DOTALL)
    # Block comments: <# ... #>
    cmd = re.sub(r"<#.*?#>", " ", cmd, flags=re.DOTALL)
    # Line comments
    cmd = re.sub(r"#.*$", " ", cmd, flags=re.MULTILINE)
    # Single- and double-quoted strings (handles backtick escapes)
    cmd = re.sub(r"'[^']*'", " ", cmd)
    cmd = re.sub(r'"([^"`]|`.)*"', " ", cmd)
    return cmd


def _detect_obfuscation(command: str) -> bool:
    """Detect PowerShell obfuscation and AMSI bypass patterns."""
    low = command.lower()
    return bool(
        re.search(
            r"amsiinitfailed|amsi.*bypass|\[ref\]\.assembly|"
            r"amsiutils|powerkatz|virtualprotect|"
            r"writeprocessmemory|mimikatz",
            low,
        )
    )


def classify_powershell_security(command: str) -> RiskLevel:
    """Classify the risk level of a PowerShell command.

    Returns one of: "critical", "high", "medium", "safe", "low".
    """
    cmd = command.strip()
    if not cmd:
        return "safe"

    clean = _strip_ps_comments_and_strings(cmd)

    if _detect_obfuscation(clean):
        return "critical"

    if _CRITICAL_RE.search(clean):
        return "critical"

    if _HIGH_RE.search(clean):
        return "high"

    if _MEDIUM_RE.search(clean):
        return "medium"

    # Check if first token is a known safe command
    tokens = clean.split()
    if tokens:
        first = tokens[0]
        if first in _LOW_RISK_COMMANDS:
            return "safe"
        if len(tokens) > 1 and f"{first} {tokens[1]}" in _LOW_RISK_COMMANDS:
            return "safe"

    return "low"


@dataclass
class PowerShellSecurityContext:
    """Full security assessment result for a PowerShell command."""

    command: str
    risk_level: RiskLevel
    auto_approve: bool = False
    reason: str = ""


def assess_powershell_command(
    command: str,
    *,
    allow_high_risk: bool = False,
    allow_medium_risk: bool = False,
) -> PowerShellSecurityContext:
    """Run a full security assessment on a PowerShell command.

    Returns a PowerShellSecurityContext with the risk level, whether the
    command is safe for auto-approval, and a human-readable reason string.
    """
    risk = classify_powershell_security(command)

    if risk == "critical":
        return PowerShellSecurityContext(command, risk, False, "Critical: irreversible system modification or AMSI bypass")
    if risk == "high":
        ok = allow_high_risk
        return PowerShellSecurityContext(command, risk, ok, "High risk: eval-like, force-delete, or policy bypass")
    if risk == "medium":
        ok = allow_high_risk or allow_medium_risk
        return PowerShellSecurityContext(command, risk, ok, "Medium risk: writes, network, or process termination")
    return PowerShellSecurityContext(command, risk, True, "Safe or low risk")
