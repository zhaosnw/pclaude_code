"""
Inbound notifications from MCP channel servers (`notifications/claude/channel`).

Port of: src/services/mcp/channelNotification.ts

Handles real-time channel notifications from MCP servers that support
the channel protocol (e.g., Slack, GitHub notifications). Supports
both channel messages and permission request notifications.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

CHANNEL_MESSAGE_METHOD = "notifications/claude/channel"
CHANNEL_PERMISSION_METHOD = "notifications/claude/channel/permission"
CHANNEL_TAG = "channel"

# Registered handlers for channel messages
_channel_handlers: list[Callable[[str, dict[str, Any]], Any]] = []
_permission_handlers: list[Callable[[str, str, str, str], Any]] = []


def channel_message_notification_schema() -> dict[str, Any]:
    return {
        "method": CHANNEL_MESSAGE_METHOD,
        "params": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "meta": {"type": "object"},
            },
        },
    }


def channel_permission_notification_schema() -> dict[str, Any]:
    return {
        "method": CHANNEL_PERMISSION_METHOD,
        "params": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "behavior": {"type": "string", "enum": ["allow", "deny"]},
            },
        },
    }


def register_channel_handler(handler: Callable[[str, dict[str, Any]], Any]) -> None:
    """Register a handler for channel message notifications."""
    _channel_handlers.append(handler)


def unregister_channel_handler(handler: Callable[[str, dict[str, Any]], Any]) -> None:
    """Remove a channel message handler."""
    if handler in _channel_handlers:
        _channel_handlers.remove(handler)


def register_permission_handler(handler: Callable[[str, str, str, str], Any]) -> None:
    """Register a handler for channel permission notifications."""
    _permission_handlers.append(handler)


async def handle_channel_notification(server_name: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Handle an incoming channel notification from an MCP server.

    Dispatches to registered channel handlers. Returns any response
    that should be sent back to the server.
    """
    results = []
    for handler in _channel_handlers:
        try:
            result = handler(server_name, payload)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                results.append(result)
        except Exception:
            pass

    if results:
        return results[-1] if len(results) == 1 else results
    return None


async def handle_channel_permission(server_name: str, request_id: str, behavior: str, channel_id: str) -> Optional[dict[str, Any]]:
    """Handle a channel permission request notification.

    Dispatches to registered permission handlers. Returns the permission
    decision that should be sent back to the server.
    """
    results = []
    for handler in _permission_handlers:
        try:
            result = handler(server_name, request_id, behavior, channel_id)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                results.append(result)
        except Exception:
            pass

    if results:
        return results[-1] if len(results) == 1 else results
    return {"request_id": request_id, "behavior": behavior, "allowed": True}
