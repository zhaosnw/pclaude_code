"""Hare Desktop config path and MCP servers (`claudeDesktop.ts`)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from hare.utils.errors import get_errno_code
from hare.utils.json_utils import safe_parse_json
from hare.utils.log import log_error
from hare.utils.platform import get_platform

# TS: Hare Desktop integration only on macOS and WSL
SUPPORTED_PLATFORMS_DESKTOP = frozenset({"macos", "wsl"})


def _strip_drive(path: str) -> str:
    return re.sub(r"^[A-Z]:", "", path, count=1)


async def get_hare_desktop_config_path() -> str:
    plat = get_platform()
    if plat not in SUPPORTED_PLATFORMS_DESKTOP:
        raise RuntimeError(
            f"Unsupported platform: {plat} - Hare Desktop integration only works on macOS and WSL.",
        )

    if plat == "macos":
        return str(
            Path.home() / "Library/Application Support/Hare/hare_desktop_config.json"
        )

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        wsl_path = userprofile.replace("\\", "/")
        wsl_path = _strip_drive(wsl_path)
        config_path = f"/mnt/c{wsl_path}/AppData/Roaming/Hare/hare_desktop_config.json"
        p = Path(config_path)
        if p.is_file():
            return str(p)

    users_dir = Path("/mnt/c/Users")
    if users_dir.is_dir():
        skip = {"Public", "Default", "Default User", "All Users"}
        for user in users_dir.iterdir():
            if not user.is_dir() or user.name in skip:
                continue
            candidate = user / "AppData/Roaming/Hare/hare_desktop_config.json"
            if candidate.is_file():
                return str(candidate)

    raise RuntimeError(
        "Could not find Hare Desktop config file in Windows. Make sure Hare Desktop is installed on Windows.",
    )


def _mcp_stdio_schema_stub(data: dict[str, Any]) -> Any:
    return data


async def read_hare_desktop_mcp_servers() -> dict[str, Any]:
    if get_platform() not in SUPPORTED_PLATFORMS_DESKTOP:
        raise RuntimeError(
            "Unsupported platform - Hare Desktop integration only works on macOS and WSL.",
        )
    try:
        path = await get_hare_desktop_config_path()
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            if get_errno_code(e) == "ENOENT":
                return {}
            raise
        config = safe_parse_json(raw)
        if not isinstance(config, dict):
            return {}
        mcp = config.get("mcpServers")
        if not isinstance(mcp, dict):
            return {}
        servers: dict[str, Any] = {}
        for name, server_config in mcp.items():
            if isinstance(server_config, dict):
                servers[name] = _mcp_stdio_schema_stub(server_config)
        return servers
    except Exception as e:  # noqa: BLE001
        log_error(e)
        return {}
