"""
Message mappers – convert between internal and SDK message formats.

Port of: src/utils/messages/mappers.ts
"""

from __future__ import annotations

from typing import Any


def map_sdk_message(sdk_msg: dict[str, Any]) -> dict[str, Any]:
    """Convert an SDK message to internal format."""
    msg_type = sdk_msg.get("type", "")
    if msg_type == "user":
        return {
            "type": "user",
            "content": sdk_msg.get("message", {}).get("content", ""),
        }
    if msg_type == "assistant":
        return {
            "type": "assistant",
            "content": sdk_msg.get("message", {}).get("content", []),
        }
    return sdk_msg


def map_to_sdk_message(internal_msg: dict[str, Any]) -> dict[str, Any]:
    """Convert an internal message to SDK format."""
    msg_type = internal_msg.get("type", "")
    if msg_type == "user":
        return {
            "type": "user",
            "message": {"role": "user", "content": internal_msg.get("content", "")},
        }
    if msg_type == "assistant":
        return {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": internal_msg.get("content", []),
            },
        }
    return internal_msg
