"""Lightweight semantic hints for PowerShell commands. Port of: src/tools/PowerShellTool/commandSemantics.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandSemantics:
    reads_files: bool = False
    writes_files: bool = False
    runs_network: bool = False


def infer_command_semantics(command: str) -> CommandSemantics:
    c = command.lower()
    return CommandSemantics(
        reads_files="get-content" in c or "cat " in c,
        writes_files="set-content" in c or "out-file" in c,
        runs_network="invoke-webrequest" in c or "curl" in c or "wget" in c,
    )
