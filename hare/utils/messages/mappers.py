"""SDK message ↔ internal message mappers. Port of: src/utils/messages/mappers.ts

Provides bidirectional conversion between SDK wire-format message dicts and
internal Message dataclass instances.  Also handles compact-metadata
serialization and content-block-level transformations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from hare.app_types.message import (
    APIMessage,
    AssistantMessage,
    AttachmentMessage,
    Message,
    ProgressMessage,
    SystemMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Content-block mappers (internal ↔ SDK wire format)
# ---------------------------------------------------------------------------

# Block types that carry tool-use metadata and need id/name preservation.
_TOOL_USE_BLOCK_TYPE = "tool_use"
_TOOL_RESULT_BLOCK_TYPE = "tool_result"
_THINKING_BLOCK_TYPE = "thinking"
_REDACTED_THINKING_BLOCK_TYPE = "redacted_thinking"
_TEXT_BLOCK_TYPE = "text"
_IMAGE_BLOCK_TYPE = "image"
_IMAGE_URL_BLOCK_TYPE = "image_url"


def _map_content_block_to_internal(
    block: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a single SDK content block into the internal canonical form.

    In most cases the wire format is already our internal format, but some
    SDK transport layers use slightly different key names (e.g. ``toolUse``
    vs ``tool_use``).  This function ensures every block uses the snake_case
    keys that the rest of the system expects.
    """
    block_type = block.get("type", "")

    # --- tool_use -----------------------------------------------------------
    if block_type in ("tool_use", "toolUse", "tool-use"):
        return {
            "type": "tool_use",
            "id": block.get("id", block.get("toolUseId", "")),
            "name": block.get("name", ""),
            "input": block.get("input", {}),
        }

    # --- tool_result --------------------------------------------------------
    if block_type in ("tool_result", "toolResult", "tool-result"):
        content = block.get("content", block.get("result", ""))
        if isinstance(content, list):
            # Flatten list-of-text-blocks into a string when possible.
            text_parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if text_parts:
                content = "\n".join(text_parts)
        return {
            "type": "tool_result",
            "tool_use_id": block.get("tool_use_id", block.get("toolUseId", "")),
            "content": content,
            "is_error": block.get("is_error", block.get("isError", False)),
        }

    # --- thinking -----------------------------------------------------------
    if block_type in ("thinking",):
        return {
            "type": "thinking",
            "thinking": block.get("thinking", ""),
            "signature": block.get("signature", ""),
        }

    # --- redacted_thinking --------------------------------------------------
    if block_type in ("redacted_thinking", "redactedThinking"):
        return {
            "type": "redacted_thinking",
            "data": block.get("data", ""),
        }

    # --- image / image_url --------------------------------------------------
    if block_type in ("image", "image_url", "imageUrl"):
        source = block.get("source", {})
        return {
            "type": "image",
            "source": source,
        }

    # --- text (default / fallthrough) ---------------------------------------
    return {
        "type": "text",
        "text": block.get("text", ""),
    }


def _map_content_block_to_sdk(
    block: dict[str, Any],
) -> dict[str, Any]:
    """Convert an internal content block to SDK wire format.

    This is the inverse of ``_map_content_block_to_internal`` — it ensures
    every block uses camelCase keys where the SDK transport expects them,
    while still being compatible with the standard snake_case internal keys.
    """
    block_type = block.get("type", "text")

    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.get("id", ""),
            "name": block.get("name", ""),
            "input": block.get("input", {}),
        }
    if block_type == "tool_result":
        return {
            "type": "tool_result",
            "tool_use_id": block.get("tool_use_id", ""),
            "content": block.get("content", ""),
            "is_error": block.get("is_error", False),
        }
    if block_type == "thinking":
        return {
            "type": "thinking",
            "thinking": block.get("thinking", ""),
            "signature": block.get("signature", ""),
        }
    if block_type == "redacted_thinking":
        return {
            "type": "redacted_thinking",
            "data": block.get("data", ""),
        }
    if block_type in ("image", "image_url"):
        return {
            "type": "image",
            "source": block.get("source", {}),
        }
    # text (default)
    return {
        "type": "text",
        "text": block.get("text", ""),
    }


# ---------------------------------------------------------------------------
# High-level message mappers
# ---------------------------------------------------------------------------


def to_internal_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map SDK stream messages to internal Message shape.

    Handles all SDK message types:

    * ``assistant`` — streamed assistant content blocks (text, tool_use, thinking).
    * ``user`` — user messages, optionally flagged as synthetic / meta.
    * ``system`` — system messages (init, warnings, compact boundaries, errors).
    * ``progress`` — tool-execution progress events.
    * ``attachment`` — file-change or structured-output attachments.
    * ``tool_use_summary`` — summaries emitted after tool-use batches.

    Unknown types are logged and passed through unchanged.
    """
    out: list[dict[str, Any]] = []

    for m in messages:
        t = m.get("type", "")

        if t == "assistant":
            out.append(_to_internal_assistant(m))
        elif t == "user":
            out.append(_to_internal_user(m))
        elif t == "system":
            out.append(_to_internal_system(m))
        elif t == "progress":
            out.append(_to_internal_progress(m))
        elif t == "attachment":
            out.append(_to_internal_attachment(m))
        elif t == "tool_use_summary":
            out.append(_to_internal_tool_use_summary(m))
        else:
            logger.debug("Unknown SDK message type %r — passing through", t)
            out.append(m)

    return out


def to_sdk_messages(
    messages: list[Message],
    *,
    include_progress: bool = False,
    include_system: bool = True,
) -> list[dict[str, Any]]:
    """Convert internal Message instances to the SDK wire-format dict list.

    Parameters
    ----------
    messages:
        Internal typed messages.
    include_progress:
        When ``False`` (default), ``ProgressMessage`` entries are filtered out
        because SDK consumers typically do not expect progress events in the
        main message stream.
    include_system:
        When ``False``, ``SystemMessage`` entries are omitted.  Useful when
        building an API request payload that only accepts user/assistant roles.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        sdk = to_sdk_message(
            msg,
            include_progress=include_progress,
            include_system=include_system,
        )
        if sdk is not None:
            result.append(sdk)
    return result


def to_sdk_message(
    message: Message,
    *,
    include_progress: bool = False,
    include_system: bool = True,
) -> Optional[dict[str, Any]]:
    """Convert a single internal Message to its SDK dict representation.

    Returns ``None`` when the message should be omitted (e.g. a
    ``ProgressMessage`` when ``include_progress`` is ``False``).
    """
    if isinstance(message, UserMessage):
        return _user_to_sdk(message)
    if isinstance(message, AssistantMessage):
        return _assistant_to_sdk(message)
    if isinstance(message, SystemMessage):
        if not include_system:
            return None
        return _system_to_sdk(message)
    if isinstance(message, ProgressMessage):
        if not include_progress:
            return None
        return _progress_to_sdk(message)
    if isinstance(message, AttachmentMessage):
        return _attachment_to_sdk(message)
    logger.debug("Unhandled internal message type %r", type(message).__name__)
    return None


# ---------------------------------------------------------------------------
# Per-type internal conversion helpers (SDK dict → internal dict)
# ---------------------------------------------------------------------------


def _to_internal_assistant(m: dict[str, Any]) -> dict[str, Any]:
    message_data = m.get("message", {})
    raw_content = message_data.get("content", [])
    if isinstance(raw_content, str):
        content = [{"type": "text", "text": raw_content}]
        raw_content = [{"type": "text", "text": raw_content}]
    elif isinstance(raw_content, list):
        content = [_map_content_block_to_internal(b) for b in raw_content]
    else:
        content = []

    # Detect tool_use-only messages — they receive a dedicated flag downstream
    # in the filter pipeline, but we surface the raw blocks here.
    return {
        "type": "assistant",
        "uuid": m.get("uuid", str(uuid4())),
        "timestamp": m.get(
            "timestamp", datetime.now(timezone.utc).isoformat()
        ),
        "message": {
            "role": "assistant",
            "content": content,
            "id": message_data.get("id"),
            "stop_reason": message_data.get("stop_reason"),
            "usage": message_data.get("usage"),
        },
        "cost_usd": m.get("cost_usd", 0.0),
        "duration_ms": m.get("duration_ms", 0.0),
        "is_api_error_message": m.get("is_api_error_message", False),
        "api_error": m.get("api_error"),
        "error_details": m.get("error_details"),
    }


def _to_internal_user(m: dict[str, Any]) -> dict[str, Any]:
    message_data = m.get("message", {})
    raw_content = message_data.get("content", "")
    if isinstance(raw_content, str):
        content = raw_content
    elif isinstance(raw_content, list):
        content = [_map_content_block_to_internal(b) for b in raw_content]
    else:
        content = ""

    return {
        "type": "user",
        "uuid": m.get("uuid", str(uuid4())),
        "timestamp": m.get("timestamp", ""),
        "message": {
            "role": "user",
            "content": content,
        },
        "is_meta": m.get("isMeta", m.get("is_meta", m.get("isSynthetic", False))),
        "is_compact_summary": m.get("isCompactSummary", m.get("is_compact_summary", False)),
        "tool_use_result": m.get("tool_use_result"),
        "source_tool_assistant_uuid": m.get("source_tool_assistant_uuid"),
    }


def _to_internal_system(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "system",
        "uuid": m.get("uuid", str(uuid4())),
        "timestamp": m.get("timestamp", ""),
        "subtype": m.get("subtype", "info"),
        "content": m.get("content", m.get("message", "")),
        "compact_metadata": m.get("compact_metadata", m.get("compactMetadata")),
        "retry_attempt": m.get("retry_attempt"),
        "max_retries": m.get("max_retries"),
        "retry_in_ms": m.get("retry_in_ms"),
        "error": m.get("error"),
    }


def _to_internal_progress(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "progress",
        "uuid": m.get("uuid", str(uuid4())),
        "timestamp": m.get("timestamp", ""),
        "tool_use_id": m.get("tool_use_id", m.get("toolUseId", "")),
        "data": m.get("data"),
    }


def _to_internal_attachment(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "attachment",
        "uuid": m.get("uuid", str(uuid4())),
        "timestamp": m.get("timestamp", ""),
        "attachment": m.get("attachment", {}),
    }


def _to_internal_tool_use_summary(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "tool_use_summary",
        "uuid": m.get("uuid", str(uuid4())),
        "summary": m.get("summary", ""),
        "preceding_tool_use_ids": m.get("preceding_tool_use_ids", m.get("precedingToolUseIds", [])),
    }


# ---------------------------------------------------------------------------
# Per-type SDK conversion helpers (internal Message → SDK dict)
# ---------------------------------------------------------------------------


def _assistant_to_sdk(msg: AssistantMessage) -> dict[str, Any]:
    content = msg.message.content
    if isinstance(content, list):
        sdk_content = [_map_content_block_to_sdk(b) for b in content]
    elif isinstance(content, str):
        sdk_content = [{"type": "text", "text": content}]
    else:
        sdk_content = []

    return {
        "type": "assistant",
        "uuid": msg.uuid,
        "timestamp": msg.timestamp,
        "message": {
            "role": "assistant",
            "content": sdk_content,
            "id": getattr(msg.message, "id", None),
            "stop_reason": getattr(msg.message, "stop_reason", None),
            "usage": getattr(msg.message, "usage", None),
        },
        "cost_usd": msg.cost_usd,
        "duration_ms": msg.duration_ms,
        "is_api_error_message": msg.is_api_error_message,
        "api_error": msg.api_error,
        "error_details": msg.error_details,
    }


def _user_to_sdk(msg: UserMessage) -> dict[str, Any]:
    content = msg.message.content
    if isinstance(content, list):
        sdk_content = [_map_content_block_to_sdk(b) for b in content]
    else:
        sdk_content = content

    return {
        "type": "user",
        "uuid": msg.uuid,
        "timestamp": msg.timestamp,
        "message": {
            "role": "user",
            "content": sdk_content,
        },
        "isMeta": msg.is_meta,
        "isCompactSummary": msg.is_compact_summary,
        "tool_use_result": msg.tool_use_result,
        "source_tool_assistant_uuid": msg.source_tool_assistant_uuid,
    }


def _system_to_sdk(msg: SystemMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "system",
        "uuid": msg.uuid,
        "timestamp": msg.timestamp,
        "subtype": msg.subtype,
        "content": msg.content,
    }
    if msg.compact_metadata is not None:
        payload["compactMetadata"] = msg.compact_metadata
    if msg.retry_attempt is not None:
        payload["retryAttempt"] = msg.retry_attempt
    if msg.max_retries is not None:
        payload["maxRetries"] = msg.max_retries
    if msg.retry_in_ms is not None:
        payload["retryInMs"] = msg.retry_in_ms
    if msg.error is not None:
        payload["error"] = msg.error
    return payload


def _progress_to_sdk(msg: ProgressMessage) -> dict[str, Any]:
    return {
        "type": "progress",
        "uuid": msg.uuid,
        "timestamp": msg.timestamp,
        "toolUseId": msg.tool_use_id,
        "data": msg.data,
    }


def _attachment_to_sdk(msg: AttachmentMessage) -> dict[str, Any]:
    return {
        "type": "attachment",
        "uuid": msg.uuid,
        "timestamp": msg.timestamp,
        "attachment": msg.attachment,
    }


# ---------------------------------------------------------------------------
# Compact metadata
# ---------------------------------------------------------------------------


@dataclass
class CompactMetadata:
    """Compact boundary metadata — signals what a context-compaction pass removed.

    Port of: the compact_metadata field on SystemMessage (subtype compact_boundary).
    """

    trigger: str = ""
    """What triggered the compaction (e.g. ``auto``, ``manual``, ``error_recovery``)."""

    tokens_freed: int = 0
    """Estimated number of tokens removed by the compaction."""

    deleted_tokens: int = 0
    """Tokens actually deleted (may differ from *tokens_freed* due to rounding)."""

    deleted_tool_ids: list[str] = field(default_factory=list)
    """Tool-use IDs whose results were pruned by compaction."""

    preserved_ids: list[str] = field(default_factory=list)
    """Message UUIDs that were explicitly preserved across the boundary."""


def to_sdk_compact_metadata(meta: CompactMetadata) -> dict[str, Any]:
    """Serialize ``CompactMetadata`` to the SDK wire format.

    The output uses camelCase keys to match what SDK consumers expect inside
    a ``system`` message's ``compactMetadata`` field.
    """
    return {
        "trigger": meta.trigger,
        "tokensFreed": meta.tokens_freed,
        "deletedTokens": meta.deleted_tokens,
        "deletedToolIds": list(meta.deleted_tool_ids),
        "preservedIds": list(meta.preserved_ids),
    }


def from_sdk_compact_metadata(raw: dict[str, Any]) -> CompactMetadata:
    """Deserialize SDK compact-metadata dict into a ``CompactMetadata`` instance."""
    return CompactMetadata(
        trigger=raw.get("trigger", raw.get("trigger", "")),
        tokens_freed=raw.get("tokensFreed", raw.get("tokens_freed", 0)),
        deleted_tokens=raw.get("deletedTokens", raw.get("deleted_tokens", 0)),
        deleted_tool_ids=list(
            raw.get("deletedToolIds", raw.get("deleted_tool_ids", []))
        ),
        preserved_ids=list(
            raw.get("preservedIds", raw.get("preserved_ids", []))
        ),
    )


# ---------------------------------------------------------------------------
# Convenience: extract content blocks for API submission
# ---------------------------------------------------------------------------


def to_api_content_blocks(
    messages: list[Message],
) -> list[dict[str, Any]]:
    """Extract a flat list of SDK-ready content blocks from internal messages.

    Useful when constructing an API request payload.  Progress and system
    messages are excluded; only user and assistant content blocks are emitted.
    """
    blocks: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, (UserMessage, AssistantMessage)):
            content = msg.message.content
            if isinstance(content, list):
                blocks.extend(_map_content_block_to_sdk(b) for b in content)
            elif isinstance(content, str) and content.strip():
                blocks.append({"type": "text", "text": content})
    return blocks


def sdk_message_role(message: dict[str, Any]) -> Optional[str]:
    """Return the API role string for an SDK message dict, or ``None``.

    Uses ``message.role`` first, then falls back to inferring from ``type``.
    """
    inner = message.get("message", {})
    role = inner.get("role")
    if role in ("user", "assistant"):
        return role

    mtype = message.get("type", "")
    if mtype in ("user", "assistant"):
        return mtype
    return None
