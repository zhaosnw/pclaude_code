"""
Pure MCP server/tool name normalization and MCP content block normalization.

Port of: src/services/mcp/normalization.ts
Expanded with content block normalization for MCP tool results and API interop.
"""

from __future__ import annotations

import logging
import re
from typing import Any

CLAUDEAI_SERVER_PREFIX = "hare.ai "

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Image format magic-byte signatures (same as bridge/inbound_messages.py)
# ---------------------------------------------------------------------------

_IMAGE_MAGIC_PREFIXES: list[tuple[str, str]] = [
    ("/9j/", "image/jpeg"),
    ("iVBORw0KGgo", "image/png"),
    ("R0lGOD", "image/gif"),
    ("UklGR", "image/webp"),
    ("PHN2Zy", "image/svg+xml"),
    ("Qk0", "image/bmp"),
]

_VALID_CONTENT_BLOCK_TYPES = frozenset(
    {"text", "image", "resource", "resource_link", "tool_use", "tool_result"}
)

# Claude API limits
_MAX_TEXT_CONTENT_LENGTH = 5_000_000   # 5MB of text is excessive; cap for safety
_MAX_IMAGE_DATA_LENGTH = 20 * 1024 * 1024  # 20MB base64 image cap


# =============================================================================
# Server name normalization (existing — kept unchanged)
# =============================================================================


def normalize_name_for_mcp(name: str) -> str:
    """
    Normalize server names for API pattern ^[a-zA-Z0-9_-]{1,64}$.
    For hare.ai servers, collapse underscores and strip leading/trailing underscores.
    """
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    if name.startswith(CLAUDEAI_SERVER_PREFIX):
        normalized = re.sub(r"_+", "_", normalized)
        normalized = re.sub(r"^_|_$", "", normalized)
    return normalized


# =============================================================================
# MIME type helpers
# =============================================================================


def _detect_mime_type_from_base64(data: str) -> str | None:
    """Detect MIME type from base64-encoded image data using magic bytes."""
    if not data:
        return None
    for prefix, mime in _IMAGE_MAGIC_PREFIXES:
        if data.startswith(prefix):
            return mime
    return None


def _is_supported_image_mime(mime_type: str) -> bool:
    """Check if a MIME type is a supported image format for the Claude API."""
    if not mime_type:
        return False
    supported = frozenset(
        {"image/jpeg", "image/png", "image/gif", "image/webp"}
    )
    return mime_type.lower() in supported


def _normalize_mime_type(raw: str) -> str:
    """Normalize a MIME type string: lowercase, strip whitespace, fix common typos."""
    if not raw:
        return ""
    cleaned = raw.strip().lower()
    # Fix common case variations
    if cleaned == "image/jpg":
        return "image/jpeg"
    if cleaned == "image/svg":
        return "image/svg+xml"
    return cleaned


# =============================================================================
# Content block validation
# =============================================================================


def is_valid_content_block(block: Any) -> bool:
    """Check if a value is a well-formed content block dict with a recognised type."""
    if not isinstance(block, dict):
        return False
    block_type = block.get("type")
    if block_type not in _VALID_CONTENT_BLOCK_TYPES:
        return False
    return True


def is_text_block(block: Any) -> bool:
    """Check if a content block is a text block."""
    return isinstance(block, dict) and block.get("type") == "text"


def is_image_block(block: Any) -> bool:
    """Check if a content block is an image block (MCP or Claude API shape)."""
    return isinstance(block, dict) and block.get("type") == "image"


def is_resource_block(block: Any) -> bool:
    """Check if a content block is an embedded resource block."""
    return isinstance(block, dict) and block.get("type") == "resource"


def is_resource_link_block(block: Any) -> bool:
    """Check if a content block is a resource link block."""
    return isinstance(block, dict) and block.get("type") == "resource_link"


# =============================================================================
# Text block normalization
# =============================================================================


def sanitize_content_block_text(text: Any, max_chars: int = 100_000) -> str:
    """Sanitize and optionally truncate text from a content block.

    Handles None, non-string values, and excessive length.
    Returns a clean string suitable for display or API use.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    if len(text) > max_chars:
        logger.warning(
            "Content block text truncated from %d to %d chars",
            len(text),
            max_chars,
        )
        text = text[:max_chars] + "\n\n[Output truncated]"
    return text


def normalize_text_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize a MCP text content block.

    - Ensures the 'text' field is a non-None string
    - Sanitizes the text value
    - Preserves annotations if present
    """
    raw_text = block.get("text")
    clean_text = sanitize_content_block_text(raw_text)
    result: dict[str, Any] = {"type": "text", "text": clean_text}
    annotations = block.get("annotations")
    if annotations is not None:
        result["annotations"] = annotations
    return result


# =============================================================================
# Image block normalization
# =============================================================================


def normalize_mcp_image_to_claude(block: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an MCP image content block to a Claude API image source block.

    MCP format (from tool result):
        {"type": "image", "data": "<base64>", "mimeType": "image/png"}

    Claude API format:
        {"type": "image", "source": {"type": "base64",
         "media_type": "image/png", "data": "<base64>"}}

    Returns None if the block cannot be normalized (missing data).
    """
    mime_type = _normalize_mime_type(str(block.get("mimeType", "")))
    data = block.get("data", "")

    # Fallback: try magic-byte detection if mimeType is missing
    if not mime_type and data:
        detected = _detect_mime_type_from_base64(str(data))
        if detected:
            mime_type = detected
            logger.debug("Detected MIME type %s from base64 magic bytes", mime_type)

    if not data:
        logger.warning("MCP image block missing data — cannot normalize")
        return None

    if isinstance(data, str) and len(data) > _MAX_IMAGE_DATA_LENGTH:
        logger.warning(
            "MCP image data exceeds max length (%d > %d) — truncating",
            len(data),
            _MAX_IMAGE_DATA_LENGTH,
        )
        data = data[:_MAX_IMAGE_DATA_LENGTH]

    if not mime_type:
        mime_type = "image/png"

    if not _is_supported_image_mime(mime_type):
        logger.warning(
            "Unsupported image MIME type '%s' for Claude API — will be dropped",
            mime_type,
        )
        return None

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime_type,
            "data": str(data),
        },
    }


def normalize_image_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize an image block to the canonical Claude API shape.

    Accepts both MCP-style ({data, mimeType}) and Claude-style ({source})
    image blocks and normalizes them to the Claude API format.
    """
    # Already in Claude API format (has 'source' dict)
    source = block.get("source")
    if isinstance(source, dict) and source.get("type") == "base64":
        # Already normalized — just validate
        media_type = source.get("media_type", "")
        data = source.get("data", "")
        if not data:
            return block  # keep as-is if no data
        if not media_type:
            detected = _detect_mime_type_from_base64(str(data))
            if detected:
                source = {**source, "media_type": detected}
                return {**block, "source": source}
        return block

    # MCP format — convert to Claude API format
    converted = normalize_mcp_image_to_claude(block)
    if converted is None:
        # Fallback: return text placeholder instead of broken image
        logger.warning("Dropping un-normalizable MCP image block")
        return {"type": "text", "text": "[image: data not available]"}
    return converted


# =============================================================================
# Resource block normalization
# =============================================================================


def normalize_resource_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize an MCP embedded resource content block.

    MCP resource blocks have shape:
        {"type": "resource", "resource": {"uri": "...", "mimeType": "...",
         "text": "..." or "blob": "..."}}

    Normalizes text/blob content, annotates with uri.
    """
    resource = block.get("resource", {})
    if not isinstance(resource, dict):
        logger.warning("Resource block missing 'resource' dict")
        return {"type": "text", "text": "[resource: unavailable]"}

    uri = str(resource.get("uri", ""))
    mime_type = _normalize_mime_type(str(resource.get("mimeType", "")))

    # Normalize text content
    text = resource.get("text")
    if text is not None:
        clean = sanitize_content_block_text(text)
        result: dict[str, Any] = {
            "type": "resource",
            "resource": {
                "uri": uri,
                "mimeType": mime_type,
                "text": clean,
            },
        }
        return result

    # Normalize blob content (base64-encoded binary)
    blob = resource.get("blob")
    if blob is not None:
        if isinstance(blob, str) and len(blob) > _MAX_IMAGE_DATA_LENGTH:
            logger.warning("Resource blob exceeds max length — truncating")
            blob = blob[:_MAX_IMAGE_DATA_LENGTH]
        result = {
            "type": "resource",
            "resource": {
                "uri": uri,
                "mimeType": mime_type,
                "blob": str(blob),
            },
        }
        return result

    # Resource with neither text nor blob
    logger.debug("Resource block has no text/blob content for uri=%s", uri)
    return {**block}


def normalize_resource_link_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize an MCP resource link block.

    Resource links have shape: {"type": "resource_link", "uri": "...", "name": "..."}
    """
    uri = str(block.get("uri", ""))
    name = str(block.get("name", "")) if block.get("name") is not None else ""
    result: dict[str, Any] = {"type": "resource_link", "uri": uri}
    if name:
        result["name"] = name
    return result


# =============================================================================
# Content block collection normalization
# =============================================================================


def normalize_content_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single MCP content block of any recognised type.

    Dispatches to the appropriate type-specific normalizer.
    Falls back to returning the block unchanged for unknown types.
    """
    block_type = block.get("type", "")
    if block_type == "text":
        return normalize_text_block(block)
    elif block_type == "image":
        return normalize_image_block(block)
    elif block_type == "resource":
        return normalize_resource_block(block)
    elif block_type == "resource_link":
        return normalize_resource_link_block(block)
    else:
        logger.debug("Skipping normalization for unknown block type: %s", block_type)
        return block


def normalize_content_blocks(
    blocks: list[dict[str, Any]] | Any,
) -> list[dict[str, Any]]:
    """Normalize a list of MCP content blocks.

    - Returns an empty list for None / non-list input
    - Drops blocks that are not dicts
    - Skips unrecognized block types silently
    - Logs warnings for dropped blocks
    """
    if not isinstance(blocks, list):
        logger.debug("normalize_content_blocks received non-list input: %s", type(blocks))
        return [{"type": "text", "text": "[No valid content]"}]

    normalized: list[dict[str, Any]] = []
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            logger.debug("Dropping non-dict content block at index %d", i)
            continue

        block_type = block.get("type")
        if block_type not in _VALID_CONTENT_BLOCK_TYPES:
            logger.debug(
                "Dropping content block with unknown type '%s' at index %d",
                block_type,
                i,
            )
            continue

        try:
            normalized.append(normalize_content_block(block))
        except Exception:
            logger.exception(
                "Failed to normalize content block at index %d (type=%s)",
                i,
                block_type,
            )
            # Substitute with a safe text block explaining the error
            normalized.append(
                {"type": "text", "text": f"[Failed to process {block_type} content]"}
            )

    # Fast-path: if no blocks survived, return empty list
    if not normalized:
        logger.debug("All content blocks were dropped during normalization")
        return [{"type": "text", "text": "[No valid content]"}]

    return normalized


# =============================================================================
# MCP tool result normalization
# =============================================================================


def normalize_mcp_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize a full MCP CallToolResult.

    Expected shape:
        {"content": [...], "isError": false, "_meta": {...}}

    Returns a normalized result with:
        - content blocks normalized (text, image, resource)
        - isError preserved
        - _meta preserved (if present)
    """
    content = result.get("content", [])
    normalized_content = normalize_content_blocks(content)

    normalized: dict[str, Any] = {
        "content": normalized_content,
        "isError": bool(result.get("isError", False)),
    }

    meta = result.get("_meta")
    if meta is not None:
        normalized["_meta"] = meta

    # Preserve any extra fields the caller may have added
    for key in result:
        if key not in ("content", "isError", "_meta"):
            normalized[key] = result[key]

    return normalized


def normalize_mcp_error_result(error_message: str) -> dict[str, Any]:
    """Create a normalized MCP error result from an error string.

    Produces: {"content": [{"type": "text", "text": "..."}], "isError": true}
    """
    return {
        "content": [{"type": "text", "text": sanitize_content_block_text(error_message)}],
        "isError": True,
    }


# =============================================================================
# Content block to string conversion (for display / logging / extraction)
# =============================================================================


def content_block_to_string(block: dict[str, Any], separator: str = " ") -> str:
    """Extract a human-readable string from a single content block.

    Used for display, logging, and compact representations of MCP results.
    """
    block_type = block.get("type", "")
    if block_type == "text":
        return str(block.get("text", ""))
    elif block_type == "image":
        source = block.get("source", {})
        mime = source.get("media_type", "") if isinstance(source, dict) else ""
        return f"[image: {mime}]" if mime else "[image]"
    elif block_type == "resource":
        resource = block.get("resource", {})
        if isinstance(resource, dict):
            text = resource.get("text")
            if text is not None:
                return str(text)
            uri = resource.get("uri", "")
            return f"[resource: {uri}]" if uri else "[resource]"
        return "[resource]"
    elif block_type == "resource_link":
        uri = block.get("uri", "")
        name = block.get("name", "")
        if name:
            return f"{name} ({uri})"
        return f"[resource_link: {uri}]" if uri else "[resource_link]"
    elif block_type == "tool_use":
        tool_name = block.get("name", "unknown")
        return f"[tool_use: {tool_name}]"
    elif block_type == "tool_result":
        tool_id = block.get("tool_use_id", "")
        return f"[tool_result: {tool_id}]"
    return f"[{block_type}]"


def content_blocks_to_string(
    blocks: list[dict[str, Any]],
    separator: str = "\n",
) -> str:
    """Convert a list of normalized content blocks to a single human-readable string.

    Useful for display in terminal, logging, and compact summaries.
    """
    if not blocks:
        return ""
    parts = [content_block_to_string(b) for b in blocks if isinstance(b, dict)]
    return separator.join(parts)


def extract_text_from_content_blocks(blocks: list[dict[str, Any]]) -> str:
    """Extract all text content from a list of content blocks, joined by newlines.

    Non-text blocks are represented by a short placeholder.
    """
    return content_blocks_to_string(blocks, separator="\n")


# =============================================================================
# Tool input normalization (server name / tool name for API calls)
# =============================================================================


def normalize_tool_name_for_mcp(tool_name: str) -> str:
    """Normalize an MCP tool name to match the API server naming pattern.

    Strip illegal characters, collapse underscores, trim to 64 chars.
    """
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name)
    normalized = re.sub(r"_+", "_", normalized)
    normalized = re.sub(r"^_|_$", "", normalized)
    if len(normalized) > 64:
        normalized = normalized[:64]
    return normalized


def normalize_server_name_display(name: str) -> str:
    """Convert a normalized server name into a human-friendly display string.

    Replaces underscores/hyphens with spaces, title-cases each word.
    Matches the behavior in hare.services.mcp.utils.format_server_name.
    """
    words = re.split(r"[_-]+", name)
    return " ".join(w.title() for w in words if w)
