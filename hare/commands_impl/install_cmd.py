"""
/install command — install / upgrade / reinstall the CLI.

Port of: src/commands/install/install.tsx (299 lines)
"""

from __future__ import annotations

import os
import sys
from typing import Any

COMMAND_NAME = "install"
DESCRIPTION = "Install or upgrade the CLI"
ALIASES: list[str] = []


def _detect_install_method() -> str:
    if "site-packages" in __file__:
        return "pip"
    exe = sys.executable
    if ".local/pipx" in exe:
        return "pipx"
    if "uv" in os.environ.get("_", ""):
        return "uv"
    return "unknown"


async def call(args: str, **context: Any) -> dict[str, Any]:
    current_version = context.get("current_version", "unknown")
    check_version = context.get("check_version")
    method = _detect_install_method()

    # Check for updates
    if check_version:
        try:
            latest = await check_version()
            if latest and latest.get("update_available"):
                cmd = {
                    "pip": "pip install --upgrade hare",
                    "pipx": "pipx upgrade hare",
                    "uv": "uv pip install --upgrade hare",
                }.get(method, "pip install --upgrade hare")
                return {
                    "type": "text",
                    "value": (
                        f"**Current:** {current_version}\n"
                        f"**Latest:** {latest.get('version', '?')}\n\n"
                        f"Update available! Run:\n```bash\n{cmd}\n```"
                    ),
                }
        except Exception:
            pass

    cmd_map = {
        "pip": "pip install hare",
        "pipx": "pipx install hare",
        "uv": "uv pip install hare",
    }
    cmd = cmd_map.get(method, "pip install hare")
    lines = [
        "## Installation",
        "",
        f"**Version:** {current_version}",
        f"**Method:** {method}",
        "",
        "### Install",
        f"```bash\n{cmd}\n```",
        "",
        "### Upgrade",
        f"```bash\n{cmd.replace('install', 'install --upgrade') if 'install' in cmd else cmd}\n```",
        "",
        "### Reinstall",
        "```bash\npip install --force-reinstall hare\n```",
        "",
        "Use `/upgrade` to check for updates.",
    ]
    return {"type": "text", "value": "\n".join(lines)}
