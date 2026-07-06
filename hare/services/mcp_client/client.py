"""
MCP client – manages connections to MCP servers.

Port of: src/services/mcp/mcpClient.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPClient:
    servers: dict[str, Any] = field(default_factory=dict)
    _started: bool = False

    async def start(self) -> None:
        """Start all configured MCP servers. Stub."""
        self._started = True

    async def stop(self) -> None:
        """Stop all MCP servers."""
        self.servers.clear()
        self._started = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all tools from connected MCP servers."""
        tools: list[dict[str, Any]] = []
        for server_name, server in self.servers.items():
            server_tools = server.get("tools", [])
            for t in server_tools:
                tools.append({**t, "server": server_name})
        return tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call a tool on a specific MCP server. Stub."""
        return {"error": f"MCP server '{server_name}' not connected"}


_instance: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    global _instance
    if _instance is None:
        _instance = MCPClient()
    return _instance
