"""
MCP service types.

Port of: src/services/mcp/types.ts

Defines configuration schemas and types for MCP (Model Context Protocol) servers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

ConfigScope = Literal[
    "local", "user", "project", "dynamic", "enterprise", "hare_ai", "managed"
]
Transport = Literal["stdio", "sse", "sse-ide", "http", "ws", "sdk", "claudeai-proxy"]
ConnectionStatus = Literal["connected", "failed", "needs_auth", "pending", "disabled"]


@dataclass
class McpStdioServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    type: str = "stdio"


@dataclass
class McpSseServerConfig:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    headers_helper: Optional[str] = None
    type: str = "sse"


@dataclass
class McpHttpServerConfig:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    headers_helper: Optional[str] = None
    type: str = "http"


@dataclass
class McpWebSocketServerConfig:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    headers_helper: Optional[str] = None
    type: str = "ws"


McpServerConfig = Union[
    McpStdioServerConfig,
    McpSseServerConfig,
    McpHttpServerConfig,
    McpWebSocketServerConfig,
]


@dataclass
class ScopedMcpServerConfig:
    scope: ConfigScope
    config: McpServerConfig
    enabled: bool = True


@dataclass
class McpToolInfo:
    """Reflected MCP tool metadata (matching TS MCPTool wrapper fields)."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""
    annotations: dict[str, Any] = field(default_factory=dict)
    is_mcp: bool = True


@dataclass
class MCPServerConnection:
    name: str
    config: McpServerConfig
    scope: ConfigScope = "user"
    enabled: bool = True
    status: ConnectionStatus = "pending"
    connected: bool = False  # legacy compat
    error: Optional[str] = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    mcp_tools: list[McpToolInfo] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    server_info: dict[str, Any] = field(default_factory=dict)

    @property
    def is_connected(self) -> bool:
        return self.status == "connected"


@dataclass
class ServerResource:
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPCliState:
    servers: list[MCPServerConnection] = field(default_factory=list)
    initialized: bool = False

    def get_server(self, name: str) -> Optional[MCPServerConnection]:
        for s in self.servers:
            if s.name == name:
                return s
        return None

    def get_enabled_servers(self) -> list[MCPServerConnection]:
        return [s for s in self.servers if s.enabled]

    def get_connected_servers(self) -> list[MCPServerConnection]:
        return [s for s in self.servers if s.connected]


@dataclass
class SerializedTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPProgress:
    type: str = ""
    server_name: str = ""
    tool_name: str = ""
    progress: float = 0.0
    total: float = 0.0
    message: str = ""
