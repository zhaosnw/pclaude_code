"""
In-process MCP transport for embedded Chrome/Computer Use servers.

Port of: src/services/mcp/InProcessTransport.ts

Provides direct in-process communication with MCP servers that run
inside the same process (e.g., Chrome DevTools Protocol, Computer Use).
No subprocess or network transport needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class InProcessTransport:
    """In-process transport for embedded MCP servers.

    Reads/writes JSON-RPC messages directly in memory without
    subprocess or network overhead. Used for built-in servers
    like Chrome DevTools Protocol and Computer Use.
    """

    name: str = ""
    _server: Any = field(default=None, repr=False)
    _connected: bool = False
    _pending_requests: dict[int, Any] = field(default_factory=dict, repr=False)
    _next_id: int = 0

    async def connect(self) -> bool:
        """Initialize the in-process server."""
        if self._server is not None:
            self._connected = True
        return self._connected

    async def disconnect(self) -> None:
        """Shutdown the in-process server."""
        self._connected = False
        self._pending_requests.clear()

    async def send(self, message: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Send a JSON-RPC message and return response if it's a request."""
        if not self._connected:
            return {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Not connected"}, "id": None}

        msg_id = message.get("id")
        method = message.get("method", "")
        params = message.get("params", {})

        if self._server is None:
            return None

        try:
            if hasattr(self._server, method):
                result = await self._server.__getattribute__(method)(**params)
            elif hasattr(self._server, "handle_request"):
                result = await self._server.handle_request(method, params)
            else:
                return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method not found: {method}"}, "id": msg_id}

            if msg_id is not None:
                return {"jsonrpc": "2.0", "id": msg_id, "result": result}
            return None
        except Exception as e:
            return {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": msg_id}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a notification (no response expected)."""
        await self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_server(self, server: Any) -> None:
        """Register the in-process server implementation."""
        self._server = server
