"""
REPL bridge handle — global singleton for cross-module access.

Port of: src/bridge/replBridgeHandle.ts

Global singleton with concurrent session registration for dedup.
"""

from __future__ import annotations

from typing import Any, Optional


class ReplBridgeHandle:
    """Handle for controlling an active REPL bridge."""

    def __init__(self, state: Any = None) -> None:
        self.state = state
        self._send_message: Any = None
        self._disconnect: Any = None

    def set_handlers(self, send_message: Any, disconnect: Any) -> None:
        self._send_message = send_message
        self._disconnect = disconnect

    async def send_message(self, msg: Any) -> None:
        if self._send_message:
            await self._send_message(msg)

    async def disconnect(self) -> None:
        if self._disconnect:
            await self._disconnect()

    def is_connected(self) -> bool:
        return self._send_message is not None


# Global singleton
_handle: Optional[ReplBridgeHandle] = None


def set_repl_bridge_handle(handle: ReplBridgeHandle) -> None:
    global _handle
    _handle = handle


def get_repl_bridge_handle() -> Optional[ReplBridgeHandle]:
    return _handle


def clear_repl_bridge_handle() -> None:
    global _handle
    _handle = None


def get_self_bridge_compat_id(to_compat_session_id: Any = None) -> str:
    """Get the bridge's session ID in compat format."""
    if not _handle or not _handle.state:
        return ""
    bridge_id = _handle.state.get("bridge_id", "")
    if bridge_id and to_compat_session_id:
        return to_compat_session_id(bridge_id)
    return bridge_id
