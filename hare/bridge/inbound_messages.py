"""
Inbound message processing — extract fields, normalize image blocks.

Port of: src/bridge/inboundMessages.ts

Extracts content and UUID from inbound SDK messages.
Normalizes image blocks with camelCase mediaType -> snake_case media_type.
"""

from __future__ import annotations

from typing import Any, Optional


def extract_inbound_message_fields(msg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extract content and UUID from an inbound user message.

    Returns None if the message should be skipped (non-user, missing/empty content).
    """
    if msg.get("type") != "user":
        return None

    content = msg.get("message", {}).get("content")
    if not content:
        return None
    if isinstance(content, list) and len(content) == 0:
        return None

    msg_uuid = msg.get("uuid") if isinstance(msg.get("uuid"), str) else None

    return {
        "content": normalize_image_blocks(content)
        if isinstance(content, list)
        else content,
        "uuid": msg_uuid,
    }


def normalize_image_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize image content blocks.

    Bridge clients may send `mediaType` (camelCase) instead of
    `media_type` (snake_case). Fix these to avoid API errors.
    Fast-path: returns original list if no normalization needed.
    """
    if not any(_is_malformed_base64_image(b) for b in blocks):
        return blocks

    result: list[dict[str, Any]] = []
    for block in blocks:
        if not _is_malformed_base64_image(block):
            result.append(block)
            continue
        source = block.get("source", {})
        media_type = source.get("mediaType", "")
        if not media_type:
            media_type = _detect_image_format(source.get("data", ""))
        result.append(
            {
                **block,
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": source.get("data", ""),
                },
            }
        )
    return result


def _is_malformed_base64_image(block: dict[str, Any]) -> bool:
    """Check if block is an image with base64 source but missing media_type."""
    if block.get("type") != "image":
        return False
    source = block.get("source", {})
    if source.get("type") != "base64":
        return False
    return not source.get("media_type")


def _detect_image_format(data: str) -> str:
    """Detect image format from base64 prefix."""
    if data.startswith("/9j/"):
        return "image/jpeg"
    if data.startswith("iVBORw0KGgo"):
        return "image/png"
    if data.startswith("R0lGOD"):
        return "image/gif"
    if data.startswith("UklGR"):
        return "image/webp"
    return "image/png"
