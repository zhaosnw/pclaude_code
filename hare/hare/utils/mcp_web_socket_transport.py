"""MCP over WebSocket transport. Port of: mcpWebSocketTransport.ts"""

from __future__ import annotations

from typing import Any, Protocol


class McpWebSocketTransport(Protocol):
    async def send(self, msg: dict[str, Any]) -> None: ...
    async def close(self) -> None: ...
