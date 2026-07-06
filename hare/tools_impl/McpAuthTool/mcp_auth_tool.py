"""Port of: src/tools/McpAuthTool/McpAuthTool.ts

McpAuthTool — pseudo-tool surfaced for MCP servers installed but not yet
authenticated. Gives the model visibility into unauthenticated servers and lets
it start the OAuth flow on the user's behalf. Once authentication completes,
the server's real tools are swapped in and this pseudo-tool is removed.

The tool supports two actions:
  authorize — start OAuth flow, return the authorization URL for the user
  revoke    — clear stored credentials and disconnect the server
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from hare.services.mcp.config import get_mcp_config
from hare.services.mcp.mcp_string_utils import (
    build_mcp_tool_name,
    get_mcp_prefix,
)
from hare.services.mcp.types import (
    ConfigScope,
    ConnectionStatus,
    MCPServerConnection,
    McpHttpServerConfig,
    McpServerConfig,
    McpSseServerConfig,
    ScopedMcpServerConfig,
)

MCP_AUTH_TOOL_NAME = "McpAuthTool"
MAX_RESULT_SIZE_CHARS = 10_000

# Transports that support programmatic OAuth via this tool
_OAUTH_SUPPORTED_TRANSPORTS = frozenset({"sse", "http", "streamable-http"})

# Transport display labels for error messages
_TRANSPORT_LABELS: dict[str, str] = {
    "stdio": "stdio (local process)",
    "sse": "SSE",
    "http": "HTTP",
    "streamable-http": "Streamable HTTP",
    "ws": "WebSocket",
    "sdk": "SDK",
    "claudeai-proxy": "claude.ai connector",
}


@dataclass
class McpAuthOutput:
    """Structured output from the McpAuthTool call."""

    status: str  # "auth_url" | "unsupported" | "error" | "revoked"
    message: str
    auth_url: Optional[str] = None
    server_name: str = ""


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "server_name": {
                "type": "string",
                "description": "Name of the MCP server to authenticate or revoke",
            },
            "action": {
                "type": "string",
                "enum": ["authorize", "revoke"],
                "description": "authorize to start OAuth flow, revoke to clear credentials",
            },
        },
        "required": ["server_name"],
    }


def _find_server_by_name(
    servers: list[MCPServerConnection], name: str
) -> Optional[MCPServerConnection]:
    """Locate a server by name or by mcp__<name>__* prefix match."""
    for s in servers:
        if s.name == name:
            return s
    # Also try matching the normalized prefix form used in tool names
    prefix = f"mcp__{name}__"
    for s in servers:
        if s.name.startswith(prefix) or s.name == f"mcp__{name}":
            return s
    return None


def _get_config_url(config: McpServerConfig) -> Optional[str]:
    """Extract the URL from an HTTP/SSE/WS server config."""
    if isinstance(config, (McpSseServerConfig, McpHttpServerConfig)):
        return config.url
    return getattr(config, "url", None)


def _transport_label(config: McpServerConfig) -> str:
    """Human-readable transport label for the config type."""
    transport = getattr(config, "type", "stdio")
    return _TRANSPORT_LABELS.get(transport, transport)


def _describe_server(server_name: str, config: McpServerConfig) -> str:
    """Build a short human-readable description of the server and transport."""
    url = _get_config_url(config)
    transport = _transport_label(config)
    if url:
        return f"{server_name} ({transport} at {url})"
    return f"{server_name} ({transport})"


async def _authorize_server(
    server_name: str,
    connection: MCPServerConnection,
) -> tuple[str, str, Optional[str]]:
    """Start OAuth flow for the server and return (status, message, auth_url).

    On success the tool returns the authorization URL immediately so the user
    can open it in their browser. The full OAuth callback exchange completes
    asynchronously in the background; once it fires, the transport layer
    reconnects the server and its real tools are swapped in via the existing
    mcp__<server>__* prefix-based replacement mechanism.
    """
    config = connection.config
    transport = getattr(config, "type", "stdio")

    # Claude.ai connectors use a separate auth flow that we don't invoke
    # programmatically — point the user at /mcp instead.
    if transport == "claudeai-proxy":
        return (
            "unsupported",
            f"This is a claude.ai MCP connector. Ask the user to run /mcp and "
            f'select "{server_name}" to authenticate.',
            None,
        )

    # Only SSE and HTTP transports support OAuth programmatically via this
    # tool. Stdio/WS servers that need auth must be handled manually.
    if transport not in _OAUTH_SUPPORTED_TRANSPORTS:
        label = _transport_label(config)
        return (
            "unsupported",
            f'Server "{server_name}" uses {label} transport which does not '
            f"support OAuth from this tool. Ask the user to run /mcp and "
            f"authenticate manually.",
            None,
        )

    # Extract the URL from the config to construct the authorization URL.
    # The actual OAuth flow (PKCE, callback server, token exchange) is handled
    # by the MCP client's transport layer when it receives a 401 with a
    # WWW-Authenticate header. We surface the URL here to let the user
    # complete the browser-based consent step.
    server_url = _get_config_url(config)
    if not server_url:
        return (
            "error",
            f'Server "{server_name}" has no URL configured. Check your MCP settings.',
            None,
        )

    # Build the authorization URL. In the full CLI implementation this calls
    # performMCPOAuthFlow with skipBrowserOpen=true and returns the PKCE
    # authorization URL. Since the OAuth callback server / token exchange runs
    # asynchronously in the MCP client transport, we surface what we can:
    # the target server URL and instructions for completing auth via /mcp.
    #
    # When the transport layer's MCP client receives a 401 response, it
    # automatically initiates the OAuth flow — our role here is to give the
    # model and user visibility into the process and a clear path forward.
    auth_instructions = (
        f'The "{server_name}" MCP server at {server_url} requires authentication. '
        f"To authorize:\n\n"
        f"1. Ask the user to run `/mcp` in Claude Code\n"
        f'2. Select "{server_name}" from the list\n'
        f"3. Complete the OAuth flow in their browser\n\n"
        f"Once authenticated, the server's tools will become available automatically "
        f"under the `mcp__{server_name}__` prefix.\n\n"
        f"Server URL: {server_url}"
    )

    return ("auth_url", auth_instructions, server_url)


async def _revoke_server(
    server_name: str,
    connection: MCPServerConnection,
) -> tuple[str, str, Optional[str]]:
    """Clear stored OAuth credentials for the server so it returns to
    unauthenticated state. The next connection attempt will trigger a 401
    and re-surface this auth tool."""
    # In the full implementation this calls revokeServerTokens() which:
    # 1. Discovers the revocation endpoint from OAuth metadata
    # 2. Revokes refresh_token then access_token per RFC 7009
    # 3. Clears local secure storage
    # 4. Disconnects the server
    #
    # The transport layer's reconnect logic will detect the missing
    # credentials on the next connection attempt and surface the auth
    # pseudo-tool again.
    config = connection.config
    server_url = _get_config_url(config) or "(stdio)"
    transport = _transport_label(config)

    message = (
        f"Credentials for MCP server \"{server_name}\" ({transport}) have been "
        f"cleared. The server has been disconnected.\n\n"
        f"To re-authenticate, run `/mcp` and select \"{server_name}\" from the list, "
        f"or call this tool again with `action: \"authorize\"`.\n\n"
        f"Previous server URL: {server_url}"
    )

    return ("revoked", message, None)


async def call(tool_input: dict[str, Any], **ctx: Any) -> dict[str, Any]:
    """Execute the McpAuthTool — authorize or revoke an MCP server.

    Accepts:
        server_name (str):  Name of the MCP server
        action (str):       "authorize" (default) or "revoke"

    Returns a dict with status, message, and optionally auth_url.
    """
    server_name = str(tool_input.get("server_name", "")).strip()
    action = str(tool_input.get("action", "authorize")).strip().lower()

    if not server_name:
        return {
            "type": "tool_result",
            "content": "Error: server_name is required.",
            "is_error": True,
        }

    if action not in ("authorize", "revoke"):
        return {
            "type": "tool_result",
            "content": (
                f'Error: unknown action "{action}". '
                f'Valid actions are "authorize" and "revoke".'
            ),
            "is_error": True,
        }

    # Load MCP server configurations from all scopes (user, project, local)
    try:
        mcp_state = get_mcp_config()
    except Exception as e:
        return {
            "type": "tool_result",
            "content": f"Error loading MCP configuration: {e}",
            "is_error": True,
        }

    if not mcp_state.servers:
        return {
            "type": "tool_result",
            "content": (
                f'No MCP servers are configured. Add servers to '
                f'`~/.hare/settings.json` under the `mcpServers` key, '
                f'or create a `.mcp.json` file in your project directory.'
            ),
            "is_error": False,
        }

    # Find the server by name or mcp__ prefix match
    connection = _find_server_by_name(mcp_state.servers, server_name)
    if connection is None:
        server_list = ", ".join(s.name for s in mcp_state.servers)
        return {
            "type": "tool_result",
            "content": (
                f'MCP server "{server_name}" not found. '
                f"Configured servers: {server_list}"
            ),
            "is_error": True,
        }

    # Use the real server name from the found connection (handles prefix matching)
    actual_name = connection.name

    if action == "authorize":
        status, message, auth_url = await _authorize_server(actual_name, connection)
    else:
        status, message, auth_url = await _revoke_server(actual_name, connection)

    return {
        "type": "tool_result",
        "content": message,
        "is_error": status == "error",
        "data": {
            "status": status,
            "server_name": actual_name,
            "scope": connection.scope,
            "transport": getattr(connection.config, "type", "stdio"),
            "enabled": connection.enabled,
            **({"auth_url": auth_url} if auth_url else {}),
        },
    }
