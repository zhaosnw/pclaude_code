"""
SDK-controlled MCP transport for IDE and agent SDK integrations.

Port of: src/services/mcp/SdkControlTransport.ts

Handles MCP communication where the SDK/IDE controls transport lifecycle
and delivers messages via control requests/responses.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Optional, Callable


@dataclass
class SdkControlTransport:
    """Transport for IDE/Agent SDK MCP communication.

    The SDK controls when to connect/disconnect and delivers messages
    via control request/response messages. No network or subprocess
    involved — all communication is in-memory.
    """

    session_id: str = ""
    _connected: bool = False
    _message_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue, repr=False)
    _on_message: Optional[Callable[[dict[str, Any]], Any]] = field(default=None, repr=False)
    _on_close: Optional[Callable[[], Any]] = field(default=None, repr=False)
    _next_id: int = 0

    async def connect(self) -> None:
        """Mark transport as connected."""
        self._connected = True

    async def close(self) -> None:
        """Close transport and notify listeners."""
        self._connected = False
        if self._on_close:
            try:
                await self._on_close()
            except Exception:
                pass
        # Drain queue
        while not self._message_queue.empty():
            try:
                self._message_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and wait for response."""
        if not self._connected:
            raise RuntimeError("SDK control transport not connected")

        self._next_id += 1
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }

        # For in-process SDK transport, dispatch directly via on_message
        if self._on_message:
            try:
                result = await self._on_message(msg)
                return result
            except Exception as e:
                return {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": self._next_id}
        return None

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a notification (fire-and-forget)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        if self._on_message:
            try:
                await self._on_message(msg)
            except Exception:
                pass

    async def deliver_message(self, message: dict[str, Any]) -> None:
        """Deliver an incoming message from the SDK to the MCP server."""
        await self._message_queue.put(message)

    async def receive(self, timeout: float = 30.0) -> Optional[dict[str, Any]]:
        """Wait for and return the next message from the SDK."""
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def set_on_message(self, callback: Callable[[dict[str, Any]], Any]) -> None:
        """Set handler for outgoing messages (MCP -> SDK)."""
        self._on_message = callback

    def set_on_close(self, callback: Callable[[], Any]) -> None:
        """Set handler for transport close."""
        self._on_close = callback

    @property
    def is_connected(self) -> bool:
        return self._connected
