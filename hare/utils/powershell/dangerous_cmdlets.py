"""Cmdlet sets for PowerShell permission / suggestion gates. Port of: dangerousCmdlets.ts"""

from __future__ import annotations

FILEPATH_EXECUTION_CMDLETS = frozenset(
    {"invoke-command", "start-job", "start-threadjob", "register-scheduledjob"}
)

DANGEROUS_SCRIPT_BLOCK_CMDLETS = frozenset(
    {
        "invoke-command",
        "invoke-expression",
        "start-job",
        "start-threadjob",
        "register-scheduledjob",
        "register-engineevent",
        "register-objectevent",
        "register-wmievent",
        "new-pssession",
        "enter-pssession",
    }
)

MODULE_LOADING_CMDLETS = frozenset(
    {
        "import-module",
        "ipmo",
        "install-module",
        "save-module",
        "update-module",
        "install-script",
        "save-script",
    }
)

SHELLS_AND_SPAWNERS = frozenset(
    {
        "pwsh",
        "powershell",
        "cmd",
        "bash",
        "wsl",
        "sh",
        "start-process",
        "start",
        "add-type",
        "new-object",
    }
)

NEVER_SUGGEST = frozenset(
    SHELLS_AND_SPAWNERS | DANGEROUS_SCRIPT_BLOCK_CMDLETS | FILEPATH_EXECUTION_CMDLETS
)
