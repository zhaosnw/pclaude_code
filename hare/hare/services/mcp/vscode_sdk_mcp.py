"""
VS Code extension SDK MCP bridge — connect to MCP servers managed by VS Code.

Port of: src/services/mcp/vscodeSdkMcp.ts

Handles MCP communication where the VS Code extension manages server
lifecycle and the CLI communicates via SDK control protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class VsCodeSdkMcpBridge:
    """Bridge for MCP servers managed by the VS Code extension.

    The VS Code extension owns the server processes. The CLI
    communicates tool lists and calls via SDK control messages.
    """

    workspace_id: str = ""
    _connected: bool = False
    _tools_cache: list[dict[str, Any]] = field(default_factory=list)
    _on_request: Any = field(default=None, repr=False)

    async def connect(self) -> bool:
        """Establish connection to VS Code MCP bridge."""
        self._connected = True
        return True

    async def disconnect(self) -> None:
        """Close connection to VS Code MCP bridge."""
        self._connected = False
        self._tools_cache.clear()

    async def list_tools(self) -> list[dict[str, Any]]:
        """Request tool listing from VS Code-managed MCP servers."""
        if not self._connected:
            return []
        if self._tools_cache:
            return list(self._tools_cache)
        if self._on_request:
            try:
                result = await self._on_request("tools/list", {})
                if isinstance(result, dict):
                    tools = result.get("tools", [])
                    self._tools_cache = list(tools)
                    return tools
            except Exception:
                pass
        return []

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool on a VS Code-managed MCP server."""
        if not self._on_request:
            raise RuntimeError("VS Code MCP bridge not connected")
        return await self._on_request("tools/call", {
            "server": server_name, "tool": tool_name, "arguments": arguments,
        })

    async def list_resources(self, server_name: str = "") -> list[dict[str, Any]]:
        """List resources from VS Code-managed MCP servers."""
        if not self._on_request:
            return []
        result = await self._on_request("resources/list", {"server": server_name})
        if isinstance(result, dict):
            return result.get("resources", [])
        return []

    def set_request_handler(self, handler: Any) -> None:
        """Set the handler for SDK requests to VS Code."""
        self._on_request = handler

    @property
    def is_connected(self) -> bool:
        return self._connected
