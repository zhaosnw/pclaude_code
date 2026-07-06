"""
Dangerous PowerShell cmdlets list.

Port of: src/utils/powershell/dangerousCmdlets.ts
"""

DANGEROUS_CMDLETS: frozenset[str] = frozenset(
    [
        "Remove-Item",
        "Remove-ItemProperty",
        "Clear-Content",
        "Clear-Item",
        "Clear-ItemProperty",
        "Remove-Variable",
        "Stop-Process",
        "Stop-Service",
        "Stop-Computer",
        "Restart-Computer",
        "Restart-Service",
        "Format-Volume",
        "Initialize-Disk",
        "Set-ExecutionPolicy",
        "Invoke-Expression",
        "Invoke-Command",
        "Invoke-WmiMethod",
        "New-Service",
        "Set-Service",
        "Add-Type",
        "Register-ScheduledTask",
        "Unregister-ScheduledTask",
        "Set-MpPreference",
        "Disable-WindowsOptionalFeature",
        "Enable-WindowsOptionalFeature",
    ]
)
