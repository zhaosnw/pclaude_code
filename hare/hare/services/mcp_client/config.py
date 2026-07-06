"""
MCP server configuration loader.

Port of: src/services/mcp/config.ts
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True


def load_mcp_config(config_path: str | None = None) -> list[MCPServerConfig]:
    """Load MCP server configs from .hare/mcp.json or similar."""
    if config_path is None:
        candidates = [
            os.path.join(os.getcwd(), ".hare", "mcp.json"),
            os.path.join(os.path.expanduser("~"), ".hare", "mcp.json"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                config_path = c
                break
    if not config_path or not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    servers: list[MCPServerConfig] = []
    mc_servers = data.get("mcpServers", data.get("servers", {}))
    if isinstance(mc_servers, dict):
        for name, cfg in mc_servers.items():
            if not isinstance(cfg, dict):
                continue
            servers.append(
                MCPServerConfig(
                    name=name,
                    command=cfg.get("command", ""),
                    args=cfg.get("args", []),
                    env=cfg.get("env", {}),
                    cwd=cfg.get("cwd"),
                    enabled=cfg.get("enabled", True),
                )
            )
    return servers
