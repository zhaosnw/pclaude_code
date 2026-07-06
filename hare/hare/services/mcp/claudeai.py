"""
claude.ai MCP server integration — connect to Claude-managed MCP servers.

Port of: src/services/mcp/claudeai.ts

Provides discovery and connection to MCP servers hosted on claude.ai,
including server listing, connection management, and status tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ClaudeAiMcpServerRef:
    """Reference to a claude.ai hosted MCP server."""
    name: str
    org_id: str = ""
    server_id: str = ""
    url: str = ""
    status: str = "disconnected"
    tools_count: int = 0


@dataclass
class ClaudeAiMcpRegistry:
    """Registry of claude.ai MCP servers available to the user."""

    _servers: dict[str, ClaudeAiMcpServerRef] = field(default_factory=dict)
    _connected: set[str] = field(default_factory=set)

    def register_server(self, ref: ClaudeAiMcpServerRef) -> None:
        """Register a claude.ai MCP server."""
        self._servers[ref.name] = ref

    def unregister_server(self, name: str) -> None:
        """Remove a claude.ai MCP server."""
        self._servers.pop(name, None)
        self._connected.discard(name)

    def get_server(self, name: str) -> Optional[ClaudeAiMcpServerRef]:
        """Get a registered server by name."""
        return self._servers.get(name)

    def list_servers(self) -> list[ClaudeAiMcpServerRef]:
        """List all registered claude.ai servers."""
        return list(self._servers.values())

    def mark_connected(self, name: str) -> None:
        """Mark a server as connected."""
        if name in self._servers:
            self._servers[name].status = "connected"
            self._connected.add(name)

    def mark_disconnected(self, name: str) -> None:
        """Mark a server as disconnected."""
        if name in self._servers:
            self._servers[name].status = "disconnected"
            self._connected.discard(name)

    def is_connected(self, name: str) -> bool:
        """Check if a server is connected."""
        return name in self._connected

    def update_tools_count(self, name: str, count: int) -> None:
        """Update the tools count for a server."""
        if name in self._servers:
            self._servers[name].tools_count = count


# Global singleton
_registry: Optional[ClaudeAiMcpRegistry] = None


def get_claudeai_mcp_registry() -> ClaudeAiMcpRegistry:
    """Get the global claude.ai MCP registry."""
    global _registry
    if _registry is None:
        _registry = ClaudeAiMcpRegistry()
    return _registry


async def connect_claudeai_mcp_server(ref: ClaudeAiMcpServerRef) -> bool:
    """Connect to a claude.ai hosted MCP server.

    Returns True if the connection was successful.
    """
    if not ref.url:
        return False

    registry = get_claudeai_mcp_registry()
    try:
        # In full implementation, this would establish an SSE/WS connection
        # using the server URL and authentication from the user's claude.ai session
        registry.mark_connected(ref.name)
        return True
    except Exception:
        registry.mark_disconnected(ref.name)
        return False


async def disconnect_claudeai_mcp_server(name: str) -> None:
    """Disconnect from a claude.ai MCP server."""
    registry = get_claudeai_mcp_registry()
    registry.mark_disconnected(name)


async def fetch_claudeai_mcp_servers(org_id: str = "") -> list[ClaudeAiMcpServerRef]:
    """Fetch available claude.ai MCP servers for the user/org.

    In the full implementation, this calls the claude.ai API to get
    the list of configured MCP servers for the user's account.
    """
    # Stub: in full implementation, calls claude.ai API
    return list(get_claudeai_mcp_registry().list_servers())
