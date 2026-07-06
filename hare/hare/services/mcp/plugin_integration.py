"""
MCP plugin integration — load MCP servers from plugin manifests.

Port of: src/utils/plugins/mcpPluginIntegration.ts

Handles loading MCP server configurations from plugins:
- .mcp.json files in plugin directories
- manifest.mcpServers (string path, MCPB file, array, or inline config)
- Environment variable resolution (${CLAUDE_PLUGIN_ROOT}, ${user_config.X}, ${VAR})
- Plugin scope prefixing (plugin:<name>:<server>)
"""

from __future__ import annotations

import json
import os
from typing import Any

from hare.services.mcp.env_expansion import expand_env_vars_in_string
from hare.services.mcp.types import (
    McpServerConfig,
    McpStdioServerConfig,
    McpSseServerConfig,
    McpHttpServerConfig,
    McpWebSocketServerConfig,
    ScopedMcpServerConfig,
)

# ---------------------------------------------------------------------------
# Plugin server loading (mcpPluginIntegration.ts lines 131-212)
# ---------------------------------------------------------------------------


def load_plugin_mcp_servers(
    plugin: dict[str, Any],
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, McpServerConfig] | None:
    """Load MCP servers from a plugin's manifest.

    Loads from multiple sources in priority order (lowest first):
    1. .mcp.json in plugin directory
    2. manifest.mcpServers: string path, MCPB file, array of specs, or inline config

    Returns a dict of server_name → McpServerConfig, or None if no servers found.
    """
    if errors is None:
        errors = []

    servers: dict[str, McpServerConfig] = {}

    plugin_path = plugin.get("path", "")
    manifest = plugin.get("manifest", {})

    # 1. Check for .mcp.json in plugin directory (lowest priority)
    default_mcp = _load_mcp_servers_from_file(plugin_path, ".mcp.json")
    if default_mcp:
        servers = {**servers, **default_mcp}

    # 2. Handle manifest.mcpServers if present (higher priority)
    mcp_servers_spec = manifest.get("mcpServers")
    if mcp_servers_spec is None:
        return _maybe_return(servers)

    if isinstance(mcp_servers_spec, str):
        if _is_mcpb_source(mcp_servers_spec):
            # MCPB file — stub: MCPB loader not yet ported
            _log(f"MCPB loading not yet implemented for: {mcp_servers_spec}")
        else:
            # Path to JSON file
            loaded = _load_mcp_servers_from_file(plugin_path, mcp_servers_spec)
            if loaded:
                servers = {**servers, **loaded}

    elif isinstance(mcp_servers_spec, list):
        # Array of paths or inline configs — last-wins collision semantics
        for spec in mcp_servers_spec:
            try:
                if isinstance(spec, str):
                    if _is_mcpb_source(spec):
                        _log(f"MCPB loading not yet implemented for: {spec}")
                        continue
                    loaded = _load_mcp_servers_from_file(plugin_path, spec)
                    if loaded:
                        servers = {**servers, **loaded}
                elif isinstance(spec, dict):
                    # Inline MCP server config
                    parsed = _parse_server_config(spec)
                    if parsed and "name" in spec:
                        servers[spec["name"]] = parsed
            except Exception as exc:
                _log(f"Failed to load MCP spec for plugin: {exc}")

    elif isinstance(mcp_servers_spec, dict):
        # Direct MCP server configs: {name: config, ...}
        for name, config in mcp_servers_spec.items():
            if isinstance(config, dict):
                parsed = _parse_server_config(config)
                if parsed:
                    servers[name] = parsed

    return _maybe_return(servers)


# ---------------------------------------------------------------------------
# Extract from all plugins (mcpPluginIntegration.ts lines 366-429)
# ---------------------------------------------------------------------------


def extract_mcp_servers_from_plugins(
    plugins: list[dict[str, Any]],
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, ScopedMcpServerConfig]:
    """Extract all MCP servers from loaded plugins.

    Iterates enabled plugins, loads their MCP servers, resolves env vars,
    adds plugin scope prefix, and returns a merged dict.
    """
    if errors is None:
        errors = []

    all_servers: dict[str, ScopedMcpServerConfig] = {}

    for plugin in plugins:
        if not plugin.get("enabled", True):
            continue

        servers = load_plugin_mcp_servers(plugin, errors)
        if not servers:
            continue

        plugin_name = plugin.get("name", "unknown")
        plugin_source = plugin.get("source", plugin.get("repository", ""))

        # Resolve env vars per server (catch errors per-server)
        resolved: dict[str, McpServerConfig] = {}
        for name, config in servers.items():
            user_config = _build_mcp_user_config(plugin, name)
            try:
                resolved[name] = resolve_plugin_mcp_environment(
                    config, plugin, user_config, errors, plugin_name, name
                )
            except Exception as exc:
                if errors is not None:
                    errors.append(
                        {
                            "type": "generic-error",
                            "source": name,
                            "plugin": plugin_name,
                            "error": str(exc),
                        }
                    )

        # Cache on plugin for reuse
        plugin["mcpServers"] = servers

        # Add plugin scope
        scoped = add_plugin_scope_to_servers(resolved, plugin_name, plugin_source)
        all_servers.update(scoped)

    return all_servers


# ---------------------------------------------------------------------------
# Plugin scope prefixing (mcpPluginIntegration.ts lines 341-360)
# ---------------------------------------------------------------------------


def add_plugin_scope_to_servers(
    servers: dict[str, McpServerConfig],
    plugin_name: str,
    plugin_source: str = "",
) -> dict[str, ScopedMcpServerConfig]:
    """Add plugin scope prefix to MCP server names.

    Prefixes with 'plugin:<pluginName>:' to avoid conflicts between plugins.
    Scope is set to 'dynamic' for plugin servers.
    """
    scoped: dict[str, ScopedMcpServerConfig] = {}
    for name, config in servers.items():
        scoped_name = f"plugin:{plugin_name}:{name}"
        scoped[scoped_name] = ScopedMcpServerConfig(
            scope="dynamic",
            config=config,
            enabled=True,
        )
        # Attach plugin source info (matching TS's pluginSource field)
        if plugin_source:
            scoped[scoped_name].__dict__["plugin_source"] = plugin_source
    return scoped


# ---------------------------------------------------------------------------
# Get from specific plugin with caching (mcpPluginIntegration.ts lines 589-634)
# ---------------------------------------------------------------------------


def get_plugin_mcp_servers(
    plugin: dict[str, Any],
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, ScopedMcpServerConfig] | None:
    """Get MCP servers from a specific plugin with env resolution and scoping.

    Uses cached servers from plugin.mcpServers if available.
    """
    if not plugin.get("enabled", True):
        return None

    if errors is None:
        errors = []

    plugin_name = plugin.get("name", "unknown")
    plugin_source = plugin.get("source", plugin.get("repository", ""))

    # Use cached servers if available
    servers = plugin.get("mcpServers") or load_plugin_mcp_servers(plugin, errors)
    if not servers:
        return None

    # Resolve env vars per server
    resolved: dict[str, McpServerConfig] = {}
    for name, config in servers.items():
        user_config = _build_mcp_user_config(plugin, name)
        try:
            resolved[name] = resolve_plugin_mcp_environment(
                config, plugin, user_config, errors, plugin_name, name
            )
        except Exception as exc:
            if errors is not None:
                errors.append(
                    {
                        "type": "generic-error",
                        "source": name,
                        "plugin": plugin_name,
                        "error": str(exc),
                    }
                )

    return add_plugin_scope_to_servers(resolved, plugin_name, plugin_source)


# ---------------------------------------------------------------------------
# Env var resolution (mcpPluginIntegration.ts lines 465-582)
# ---------------------------------------------------------------------------


def resolve_plugin_mcp_environment(
    config: McpServerConfig,
    plugin: dict[str, Any],
    user_config: dict[str, str] | None = None,
    errors: list[dict[str, Any]] | None = None,
    plugin_name: str = "",
    server_name: str = "",
) -> McpServerConfig:
    """Resolve environment variables for plugin MCP server configs.

    Resolution order (matching TS):
    1. ${CLAUDE_PLUGIN_ROOT} → plugin root path
    2. ${user_config.X} → saved user config values
    3. ${VAR} / ${VAR:-default} → general environment variables

    stdio-specific:
    - CLAUDE_PLUGIN_ROOT and CLAUDE_PLUGIN_DATA are auto-added to env
    """
    all_missing: list[str] = []

    def _resolve(value: str) -> str:
        nonlocal all_missing
        # 1. Substitute plugin-specific variables
        resolved = _substitute_plugin_variables(value, plugin)
        # 2. Substitute user config variables
        if user_config:
            resolved = _substitute_user_config_variables(resolved, user_config)
        # 3. Expand general env vars
        result = expand_env_vars_in_string(resolved)
        all_missing.extend(result.missing_vars)
        return result.expanded

    transport_type = getattr(config, "type", None) or "stdio"

    resolved: McpServerConfig
    if transport_type == "stdio" or transport_type is None:
        assert isinstance(config, McpStdioServerConfig)
        resolved = McpStdioServerConfig(
            command=_resolve(config.command),
            args=[_resolve(a) for a in (config.args or [])],
            env={
                "CLAUDE_PLUGIN_ROOT": plugin.get("path", ""),
                "CLAUDE_PLUGIN_DATA": _get_plugin_data_dir(plugin.get("source", "")),
                **{k: _resolve(v) for k, v in (config.env or {}).items()},
            },
        )
    elif transport_type in ("sse", "http", "ws"):
        resolved = _resolve_remote_config(config, _resolve)
    elif transport_type in ("sse-ide", "ws-ide", "sdk", "claudeai-proxy"):
        # Pass through unchanged (matching TS lines 551-555)
        resolved = config  # type: ignore[assignment]
    else:
        resolved = config  # type: ignore[assignment]

    # Track missing vars
    if errors is not None and all_missing:
        unique_missing = list(dict.fromkeys(all_missing))
        if plugin_name and server_name:
            errors.append(
                {
                    "type": "mcp-config-invalid",
                    "source": f"plugin:{plugin_name}",
                    "plugin": plugin_name,
                    "serverName": server_name,
                    "validationError": f"Missing environment variables: {', '.join(unique_missing)}",
                }
            )

    return resolved


# ---------------------------------------------------------------------------
# Unconfigured channels (mcpPluginIntegration.ts lines 290-318)
# ---------------------------------------------------------------------------


def get_unconfigured_channels(
    plugin: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find channel entries in a plugin's manifest whose required userConfig
    fields are not yet saved.

    Returns list of {server, displayName, configSchema}.
    """
    manifest = plugin.get("manifest", {})
    channels = manifest.get("channels")
    if not channels:
        return []

    plugin_id = plugin.get("repository", plugin.get("source", ""))
    unconfigured: list[dict[str, Any]] = []

    for channel in channels:
        user_config_schema = channel.get("userConfig")
        if not user_config_schema:
            continue
        saved = _load_mcp_server_user_config(plugin_id, channel.get("server", ""))
        if not _validate_user_config(saved, user_config_schema):
            unconfigured.append(
                {
                    "server": channel.get("server", ""),
                    "displayName": channel.get(
                        "displayName", channel.get("server", "")
                    ),
                    "configSchema": user_config_schema,
                }
            )

    return unconfigured


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _maybe_return(
    servers: dict[str, McpServerConfig],
) -> dict[str, McpServerConfig] | None:
    return servers if servers else None


def _is_mcpb_source(path: str) -> bool:
    """Check if a path looks like an MCPB file (stub — MCPB loader not yet ported)."""
    return path.endswith(".mcpb") or path.endswith(".mcpb.json")


def _log(msg: str) -> None:
    """Log for debugging (stub — real impl uses logForDebugging)."""
    import logging

    logging.getLogger("hare.mcp.plugin").debug(msg)


def _load_mcp_servers_from_file(
    plugin_path: str, relative_path: str
) -> dict[str, McpServerConfig] | None:
    """Load MCP servers from a JSON file within a plugin directory.

    Supports both .mcp.json format (with mcpServers key) and flat format.
    Matching mcpPluginIntegration.ts lines 219-266.
    """
    file_path = os.path.join(plugin_path, relative_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, OSError):
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None

    # Check for .mcp.json format with mcpServers key
    mcp_servers = parsed.get("mcpServers", parsed)
    if not isinstance(mcp_servers, dict):
        return None

    validated: dict[str, McpServerConfig] = {}
    for name, config_data in mcp_servers.items():
        parsed_config = _parse_server_config(config_data)
        if parsed_config is not None:
            validated[name] = parsed_config
    return validated if validated else None


def _parse_server_config(data: Any) -> McpServerConfig | None:
    """Parse a server config dict into typed McpServerConfig. Returns None if invalid."""
    if not isinstance(data, dict):
        return None
    transport = data.get("type", "stdio")
    if transport == "stdio":
        cmd = data.get("command", "")
        if not cmd:
            return None
        return McpStdioServerConfig(
            command=cmd,
            args=data.get("args", []),
            env=data.get("env", {}),
        )
    elif transport == "sse":
        url = data.get("url", "")
        if not url:
            return None
        return McpSseServerConfig(
            url=url,
            headers=data.get("headers", {}),
        )
    elif transport in ("http", "streamable-http"):
        url = data.get("url", "")
        if not url:
            return None
        return McpHttpServerConfig(
            url=url,
            headers=data.get("headers", {}),
        )
    elif transport == "ws":
        url = data.get("url", "")
        if not url:
            return None
        return McpWebSocketServerConfig(
            url=url,
            headers=data.get("headers", {}),
        )
    return None


def _resolve_remote_config(config: McpServerConfig, resolve: Any) -> McpServerConfig:
    """Resolve URL and headers for SSE/HTTP/WS configs."""
    transport = getattr(config, "type", "sse")
    url = resolve(getattr(config, "url", ""))
    headers = {k: resolve(v) for k, v in getattr(config, "headers", {}).items()}
    if transport == "sse":
        return McpSseServerConfig(url=url, headers=headers)
    elif transport == "ws":
        return McpWebSocketServerConfig(url=url, headers=headers)
    else:
        return McpHttpServerConfig(url=url, headers=headers)


def _substitute_plugin_variables(value: str, plugin: dict[str, Any]) -> str:
    """Replace ${CLAUDE_PLUGIN_ROOT} and similar plugin-specific variables.

    Stub: real implementation in pluginOptionsStorage.ts handles more variables.
    """
    plugin_root = plugin.get("path", "")
    value = value.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
    value = value.replace("$CLAUDE_PLUGIN_ROOT", plugin_root)
    return value


def _substitute_user_config_variables(value: str, user_config: dict[str, str]) -> str:
    """Replace ${user_config.X} with saved user config values.

    Stub: real implementation matches ${user_config.KEY} pattern.
    """
    import re

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        return user_config.get(key, m.group(0))

    return re.sub(r"\$\{user_config\.([^}]+)\}", repl, value)


def _get_plugin_data_dir(plugin_source: str) -> str:
    """Get the plugin data directory (stub — real impl uses pluginDirectories.ts)."""
    import tempfile

    return os.path.join(tempfile.gettempdir(), "hare-plugin-data", plugin_source)


def _build_mcp_user_config(
    plugin: dict[str, Any], server_name: str
) -> dict[str, str] | None:
    """Build userConfig for an MCP server by merging top-level and channel-specific.

    Port of mcpPluginIntegration.ts lines 440-458.
    Channel-specific wins on collision.
    """
    manifest = plugin.get("manifest", {})
    plugin_id = plugin.get("repository", "")

    top_level_config: dict[str, str] | None = None
    if manifest.get("userConfig"):
        top_level_config = _load_plugin_options(plugin) or None

    channel_config = _load_channel_user_config(plugin, server_name)

    if not top_level_config and not channel_config:
        return None
    return {**(top_level_config or {}), **(channel_config or {})}


def _load_plugin_options(plugin: dict[str, Any]) -> dict[str, str]:
    """Load saved plugin options (stub — real impl uses pluginOptionsStorage.ts)."""
    _ = plugin  # unused in stub
    return {}


def _load_channel_user_config(
    plugin: dict[str, Any], server_name: str
) -> dict[str, str] | None:
    """Look up saved user config for a channel server (stub)."""
    manifest = plugin.get("manifest", {})
    channels = manifest.get("channels", [])
    for channel in channels:
        if channel.get("server") == server_name and channel.get("userConfig"):
            plugin_id = plugin.get("repository", "")
            return _load_mcp_server_user_config(plugin_id, server_name)
    return None


def _load_mcp_server_user_config(
    plugin_id: str, server_name: str
) -> dict[str, str] | None:
    """Load saved user config for an MCP server (stub — real impl uses mcpbHandler.ts)."""
    _ = (plugin_id, server_name)  # unused in stub
    return None


def _validate_user_config(saved: dict[str, str] | None, schema: dict[str, Any]) -> bool:
    """Validate saved user config against schema (stub)."""
    if not saved or not schema:
        return False
    # Check that all required fields in schema have values in saved
    required = schema.get("required", [])
    if isinstance(required, list):
        return all(k in saved and saved[k] for k in required)
    return True
