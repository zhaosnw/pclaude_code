"""Port of: src/commands/version.ts. Print the version this session is running."""

from __future__ import annotations

import platform
import sys
from typing import Any

from hare.constants.product import (
    BUILD_TIME,
    FEEDBACK_CHANNEL,
    PACKAGE_URL,
    VERSION,
    VERSION_CHANGELOG,
)

COMMAND_NAME = "version"
DESCRIPTION = "Print the version this session is running (not what autoupdate downloaded)"
ALIASES: list[str] = []


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Print hare version, Python runtime, platform, and architecture."""
    lines: list[str] = []

    # Hare version line
    if BUILD_TIME:
        lines.append(f"Hare {VERSION} (built {BUILD_TIME})")
    else:
        lines.append(f"Hare {VERSION}")

    # Python runtime
    lines.append(f"Python  {sys.version.split()[0]}  ({sys.implementation.name}  {sys.implementation.version})")

    # Platform and architecture
    lines.append(f"OS      {platform.system()} {platform.release()}")
    lines.append(f"Arch    {platform.machine()}  ({platform.architecture()[0]})")

    # Package and links
    lines.append("")
    lines.append(f"Package : {PACKAGE_URL}")
    lines.append(f"Changelog: {VERSION_CHANGELOG}")
    lines.append(f"Feedback  : {FEEDBACK_CHANNEL}")

    return {"type": "text", "value": "\n".join(lines)}
