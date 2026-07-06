"""MCP (Model Context Protocol) service layer."""

from hare.services.mcp.types import (
    ConfigScope,
    Transport,
    ConnectionStatus,
    McpServerConfig,
    McpStdioServerConfig,
    McpSseServerConfig,
    McpHttpServerConfig,
    McpWebSocketServerConfig,
    MCPServerConnection,
    McpToolInfo,
    ServerResource,
    MCPCliState,
    ScopedMcpServerConfig,
)
from hare.services.mcp.config import (
    get_mcp_config,
    load_mcp_servers,
    load_mcp_servers_from_settings,
)
from hare.services.mcp.client import (
    McpClientPool,
    get_mcp_client_pool,
    reset_mcp_client_pool,
    MCPError,
)
from hare.services.mcp.plugin_integration import (
    load_plugin_mcp_servers,
    extract_mcp_servers_from_plugins,
    add_plugin_scope_to_servers,
    get_plugin_mcp_servers,
    resolve_plugin_mcp_environment,
    get_unconfigured_channels,
)
from hare.services.mcp.utils import (
    format_server_name,
    validate_server_config,
)
from hare.services.mcp.auth import (
    # Error classes
    OAuthError,
    AuthenticationCancelledError,
    InvalidGrantError,
    ServerError,
    TemporarilyUnavailableError,
    TooManyRequestsError,
    TokenExchangeError,
    CallbackTimeoutError,
    CallbackStateMismatchError,
    PortUnavailableError,
    # Core types
    OAuthClientProvider,
    McpOAuthTokens,
    McpOAuthClientMetadata,
    # OAuth metadata / discovery
    discover_oauth_metadata,
    get_scope_from_metadata,
    # PKCE utilities
    generate_pkce_pair,
    generate_state,
    # Auth URL construction
    build_oauth_authorization_url,
    # Token exchange
    exchange_code_for_tokens,
    exchange_refresh_token,
    # URL redaction
    redact_sensitive_url_params,
    # Server key
    get_server_key,
    # Token revocation
    revoke_server_tokens,
    clear_server_tokens_from_storage,
    # High-level flow
    start_mcp_oauth_flow,
    perform_mcp_oauth_flow,
    refresh_mcp_oauth_tokens_if_needed,
    # Client secret management
    save_mcp_client_secret,
    get_mcp_client_config,
    clear_mcp_client_config,
    read_client_secret,
    # Status checks
    has_mcp_discovery_but_no_token,
    has_mcp_oauth_tokens,
    # Step-up auth
    extract_step_up_scope_from_www_authenticate,
)
