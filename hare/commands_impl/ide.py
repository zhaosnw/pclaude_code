"""
/ide command - manage IDE integration.

Port of: src/commands/ide/ (2 files)

Shows IDE connection status. Supports VS Code and JetBrains integrations.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "ide"
DESCRIPTION = "Manage IDE integration"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show IDE integration status."""
    get_ide_status = context.get("get_ide_status")

    if get_ide_status:
        status = await get_ide_status()
    else:
        status = {"connected": False, "ides": []}

    lines = ["## IDE Integration", ""]

    ides = status.get("ides", [])
    if ides:
        lines.append("**Connected to:**")
        for ide in ides:
            name = ide.get("name", "Unknown IDE")
            version = ide.get("version", "")
            v = f" (v{version})" if version else ""
            lines.append(f"- {name}{v}")
    else:
        lines.append("**Status:** No IDE connected")
        lines.append("")
        lines.append("Supported IDEs:")
        lines.append("- **VS Code**: Install the Claude Code extension")
        lines.append("- **JetBrains**: Install the Claude Code plugin")
        lines.append("")
        lines.append("The IDE integration provides file context and inline editing.")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
