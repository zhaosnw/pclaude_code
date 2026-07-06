"""Static permission prefix extraction for PowerShell. Port of: staticPrefix.ts"""

from __future__ import annotations

from hare.utils.powershell.dangerous_cmdlets import NEVER_SUGGEST
from hare.utils.powershell.parser import ParsedCommandElement, parse_powershell_command


async def extract_static_prefix_from_powershell(script: str) -> str | None:
    cmds = await parse_powershell_command(script)
    if not cmds:
        return None
    cmd = cmds[0]
    return await extract_prefix_from_element(cmd)


async def extract_prefix_from_element(cmd: ParsedCommandElement) -> str | None:
    if cmd.name_type == "application":
        return None
    name = cmd.name
    if not name:
        return None
    if name.lower() in NEVER_SUGGEST:
        return None
    if cmd.name_type == "cmdlet":
        return name
    return None
