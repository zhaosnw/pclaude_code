"""
MCP configuration loading from settings, .mcp.json files, and environment.

Port of: src/services/mcp/config.ts

Loads MCP server configurations from multiple scopes (lowest→highest precedence):
  hare_ai → dynamic → user → project → local → enterprise → managed
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from hare.services.mcp.env_expansion import expand_env_vars_in_string
from hare.services.mcp.types import (
    ConfigScope,
    MCPCliState,
    MCPServerConnection,
    McpHttpServerConfig,
    McpServerConfig,
    McpSseServerConfig,
    McpStdioServerConfig,
    McpWebSocketServerConfig,
    ScopedMcpServerConfig,
)

logger = logging.getLogger(__name__)

SCOPE_PRECEDENCE: dict[ConfigScope, int] = {
    "hare_ai": 0, "dynamic": 1, "user": 2, "project": 3,
    "local": 4, "enterprise": 5, "managed": 6,
}

_BUILTIN_HARE_SERVERS: dict[str, McpServerConfig] = {
    "hare-knowledge": McpStdioServerConfig(command="hare-knowledge-server", args=[], env={}),
    "hare-memory": McpStdioServerConfig(command="hare-memory-server", args=[], env={}),
}


def get_mcp_config(
    settings_dir: Optional[str] = None,
    *,
    project_dir: Optional[str] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
    enterprise_config: Optional[dict[str, Any]] = None,
) -> MCPCliState:
    """Load MCP configuration from all sources and return initialized state."""
    state = MCPCliState()
    try:
        state.servers = load_mcp_servers(
            settings_dir=settings_dir, project_dir=project_dir,
            plugins=plugins, enterprise_config=enterprise_config,
        )
    except Exception:
        logger.exception("Failed to load MCP servers")
    state.initialized = True
    return state


def load_mcp_servers(
    settings_dir: Optional[str] = None,
    project_dir: Optional[str] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
    enterprise_config: Optional[dict[str, Any]] = None,
) -> list[MCPServerConnection]:
    """Load MCP servers from all scopes, deduped by name (highest precedence wins)."""
    scoped: dict[str, ScopedMcpServerConfig] = {}
    user_home = os.path.expanduser("~")
    resolved_settings = settings_dir or os.path.join(user_home, ".hare")

    # 1. hare_ai built-ins (lowest)
    _load_hare_ai_servers(scoped)

    # 2. dynamic: plugin-provided
    if plugins:
        _load_plugin_servers(plugins, scoped)

    # 3. User: ~/.hare/settings.json
    user_config = os.path.join(resolved_settings, "settings.json")
    if os.path.isfile(user_config):
        _load_from_settings_json(user_config, "user", scoped)

    # 4. Project: walk .mcp.json from cwd up
    if project_dir:
        _load_mcp_json_chain(project_dir, scoped)

        # 5. Local: .hare/settings.local.json
        local_config = os.path.join(project_dir, ".hare", "settings.local.json")
        if os.path.isfile(local_config):
            _load_from_settings_json(local_config, "local", scoped)

    # 6. Enterprise: managed config (dict or ~/.hare/enterprise-settings.json)
    _load_enterprise_servers(enterprise_config, resolved_settings, scoped)

    # 7. Managed: ~/.hare/managed-mcp.json (highest)
    _load_managed_servers(resolved_settings, scoped)

    return [
        MCPServerConnection(name=name, config=sc.config, scope=sc.scope, enabled=sc.enabled)
        for name, sc in scoped.items()
    ]


load_mcp_servers_from_settings = load_mcp_servers


# ---------------------------------------------------------------------------
# Scope loaders
# ---------------------------------------------------------------------------


def _load_hare_ai_servers(scoped: dict[str, ScopedMcpServerConfig]) -> None:
    for name, config in _BUILTIN_HARE_SERVERS.items():
        scoped[name] = ScopedMcpServerConfig(scope="hare_ai", config=config, enabled=True)


def _load_plugin_servers(
    plugins: list[dict[str, Any]], scoped: dict[str, ScopedMcpServerConfig]
) -> None:
    try:
        from hare.services.mcp.plugin_integration import extract_mcp_servers_from_plugins
        for name, ps in extract_mcp_servers_from_plugins(plugins).items():
            _upsert_scoped(name, "dynamic", ps.config, True, scoped)
    except Exception:
        logger.exception("Failed to load plugin MCP servers")


def _load_enterprise_servers(
    enterprise_config: Optional[dict[str, Any]],
    settings_dir: str,
    scoped: dict[str, ScopedMcpServerConfig],
) -> None:
    """Load enterprise config from dict, or fall back to ~/.hare/enterprise-settings.json."""
    if enterprise_config is not None:
        _apply_server_block(enterprise_config, "enterprise", scoped)
        return

    path = os.path.join(settings_dir, "enterprise-settings.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _apply_server_block(json.load(f), "enterprise", scoped)
        except (json.JSONDecodeError, OSError):
            logger.debug("Could not read enterprise-settings.json")


def _load_managed_servers(
    settings_dir: str, scoped: dict[str, ScopedMcpServerConfig]
) -> None:
    path = os.path.join(settings_dir, "managed-mcp.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    if isinstance(data, dict):
        _apply_server_block(data, "managed", scoped)


def _apply_server_block(
    data: dict[str, Any], scope: ConfigScope, scoped: dict[str, ScopedMcpServerConfig]
) -> None:
    """Parse an 'mcpServers' block from settings data and merge into scoped."""
    mcp_block = data.get("mcpServers", data) if isinstance(data, dict) else {}
    if not isinstance(mcp_block, dict):
        return
    for name, cfg in mcp_block.items():
        if not isinstance(name, str) or not name.strip():
            continue
        config = _parse_server_config(cfg)
        if config is None:
            continue
        enabled = cfg.get("enabled", True) if isinstance(cfg, dict) else True
        _upsert_scoped(name, scope, config, enabled, scoped)


def _upsert_scoped(
    name: str,
    scope: ConfigScope,
    config: McpServerConfig,
    enabled: bool,
    scoped: dict[str, ScopedMcpServerConfig],
) -> None:
    """Insert or update a scoped server config, respecting precedence."""
    existing = scoped.get(name)
    if existing is None or SCOPE_PRECEDENCE.get(scope, 0) >= SCOPE_PRECEDENCE.get(existing.scope, 0):
        scoped[name] = ScopedMcpServerConfig(scope=scope, config=config, enabled=enabled)


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------


def _load_from_settings_json(
    path: str, scope: ConfigScope, scoped: dict[str, ScopedMcpServerConfig]
) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    mcp_servers = data.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        return

    for name, cfg in mcp_servers.items():
        if not isinstance(name, str) or not name.strip():
            continue
        config = _parse_server_config(cfg)
        if config is None:
            continue
        enabled = cfg.get("enabled", True) if isinstance(cfg, dict) else True
        _upsert_scoped(name, scope, config, enabled, scoped)


def _load_mcp_json_chain(
    project_dir: str, scoped: dict[str, ScopedMcpServerConfig]
) -> None:
    """Walk up from project_dir collecting .mcp.json files. Nearer-to-cwd wins."""
    current = Path(project_dir).resolve()
    root = Path(current.anchor)
    mcp_files: list[str] = []
    seen: set[str] = set()

    while True:
        mcp_json = current / ".mcp.json"
        mcp_str = str(mcp_json)
        if mcp_json.is_file() and mcp_str not in seen:
            seen.add(mcp_str)
            mcp_files.append(mcp_str)
        if current == root:
            break
        current = current.parent

    for path in reversed(mcp_files):
        _load_from_mcp_json(path, scoped)


def _load_from_mcp_json(path: str, scoped: dict[str, ScopedMcpServerConfig]) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    mcp_servers = data.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        return

    for name, cfg in mcp_servers.items():
        if not isinstance(name, str) or not name.strip():
            continue
        config = _parse_server_config(cfg)
        if config is None:
            continue
        _upsert_scoped(name, "project", config, True, scoped)


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def _expand_config_value(value: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} in strings, dicts, and lists."""
    if isinstance(value, str):
        result = expand_env_vars_in_string(value)
        if result.missing_vars:
            logger.debug("Unresolved env vars: %s", result.missing_vars)
        return result.expanded
    if isinstance(value, dict):
        return {k: _expand_config_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_config_value(v) for v in value]
    return value


def _parse_server_config(data: Any) -> Optional[McpServerConfig]:
    """Parse a dict into typed McpServerConfig with env var expansion.

    Returns None if required fields are missing.
    Handles: stdio, sse, http/streamable-http, ws, sse-ide, ws-ide, sdk, claudeai-proxy.
    """
    if not isinstance(data, dict):
        return None

    transport = data.get("type", "stdio")

    # Remote transports (sse, http, ws)
    if transport in ("sse", "http", "streamable-http", "ws"):
        url = str(data.get("url", ""))
        if not url:
            return None
        headers = _expand_config_value(data.get("headers", {}))
        helper = data.get("headers_helper")
        url_expanded = _expand_config_value(url)
        if transport == "sse":
            return McpSseServerConfig(url=url_expanded, headers=headers, headers_helper=helper)
        elif transport == "ws":
            return McpWebSocketServerConfig(url=url_expanded, headers=headers, headers_helper=helper)
        else:
            return McpHttpServerConfig(url=url_expanded, headers=headers, headers_helper=helper)

    # Pass-through transports resolved at connection time
    if transport in ("sse-ide", "ws-ide", "sdk", "claudeai-proxy"):
        placeholder = str(data.get("command", data.get("url", "")))
        return McpStdioServerConfig(command=placeholder, args=[], env={})

    # Default: stdio
    if transport == "stdio":
        command = str(data.get("command", ""))
        if not command:
            return None
        return McpStdioServerConfig(
            command=_expand_config_value(command),
            args=[_expand_config_value(a) for a in data.get("args", [])],
            env={str(k): _expand_config_value(str(v)) for k, v in data.get("env", {}).items()},
        )

    return None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def discover_mcp_servers(
    *,
    project_dir: Optional[str] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
    enterprise_config: Optional[dict[str, Any]] = None,
) -> MCPCliState:
    """Full discovery of MCP servers from all sources (convenience wrapper)."""
    return get_mcp_config(project_dir=project_dir, plugins=plugins, enterprise_config=enterprise_config)


def resolve_mcp_server_by_name(
    name: str, *, project_dir: Optional[str] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
) -> Optional[MCPServerConnection]:
    """Look up a single MCP server by name across all scopes."""
    for s in load_mcp_servers(project_dir=project_dir, plugins=plugins):
        if s.name == name:
            return s
    return None


def list_mcp_server_names(
    *, project_dir: Optional[str] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
    enabled_only: bool = False,
) -> list[str]:
    """List all configured MCP server names, sorted."""
    servers = load_mcp_servers(project_dir=project_dir, plugins=plugins)
    if enabled_only:
        servers = [s for s in servers if s.enabled]
    return sorted(s.name for s in servers)


def get_servers_by_scope(
    *, project_dir: Optional[str] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
) -> dict[ConfigScope, list[MCPServerConnection]]:
    """Group MCP servers by their scope."""
    result: dict[ConfigScope, list[MCPServerConnection]] = {}
    for s in load_mcp_servers(project_dir=project_dir, plugins=plugins):
        result.setdefault(s.scope, []).append(s)
    return result
