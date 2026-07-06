"""CLM (Command Language Model) type hints for PowerShell. Port of: src/tools/PowerShellTool/clmTypes.ts"""

from __future__ import annotations

from typing import Any, TypedDict


class ClmParameter(TypedDict, total=False):
    name: str
    type: str
    description: str


class ClmCommandInfo(TypedDict, total=False):
    name: str
    parameters: list[ClmParameter]


def parse_clm_metadata(_block: str) -> dict[str, Any]:
    return {}
