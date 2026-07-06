"""
PowerShell command parser.

Port of: src/utils/powershell/parser.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedPowerShellCommand:
    cmdlet: str
    arguments: list[str]
    is_pipeline: bool = False
    segments: list[str] | None = None


def parse_powershell_command(command: str) -> ParsedPowerShellCommand:
    """Parse a PowerShell command string."""
    command = command.strip()
    if "|" in command:
        segments = [s.strip() for s in command.split("|")]
        first = segments[0] if segments else command
        parts = first.split(None, 1)
        return ParsedPowerShellCommand(
            cmdlet=parts[0] if parts else "",
            arguments=parts[1:] if len(parts) > 1 else [],
            is_pipeline=True,
            segments=segments,
        )
    parts = command.split(None, 1)
    return ParsedPowerShellCommand(
        cmdlet=parts[0] if parts else "",
        arguments=parts[1:] if len(parts) > 1 else [],
    )
