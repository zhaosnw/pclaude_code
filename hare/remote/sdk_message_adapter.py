"""
SDK message adapter – convert between SDK and internal message formats.

Port of: src/remote/sdkMessageAdapter.ts
"""

from __future__ import annotations
from typing import Any


def convert_sdk_message(
    sdk_msg: dict[str, Any],
    convert_tool_results: bool = True,
    convert_user_text: bool = True,
) -> dict[str, Any] | None:
    msg_type = sdk_msg.get("type", "")
    if msg_type == "user_message" and convert_user_text:
        return {"type": "user", "content": sdk_msg.get("content", "")}
    if msg_type == "assistant_message":
        return {"type": "assistant", "content": sdk_msg.get("content", [])}
    if msg_type == "tool_result" and convert_tool_results:
        return {
            "type": "tool_result",
            "tool_use_id": sdk_msg.get("tool_use_id", ""),
            "content": sdk_msg.get("content", ""),
        }
    return None


def is_session_end_message(msg: dict[str, Any]) -> bool:
    return msg.get("type") in ("end_turn", "session_end")


def is_success_result(msg: dict[str, Any]) -> bool:
    return msg.get("stop_reason") == "end_turn"


def get_result_text(msg: dict[str, Any]) -> str:
    content = msg.get("content", [])
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)
