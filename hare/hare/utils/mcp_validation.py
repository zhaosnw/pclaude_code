"""MCP tool result size validation — port of `mcpValidation.ts`.

Handles:
  - Token threshold estimation (rough + API-based precise count)
  - Content block truncation for text / image / resource / tool_result blocks
  - Image compression fallback when an image exceeds budget
  - GrowthBook feature-flag override for max-MCP-output-tokens
  - Multi-byte safe string truncation
  - Truncation-message injection
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional, Union

from hare.services.token_estimation import (
    estimate_tokens as rough_token_count_estimation,
)
from hare.utils.log import log_error

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MCP_TOKEN_COUNT_THRESHOLD_FACTOR = 0.5
IMAGE_TOKEN_ESTIMATE = 1600
DEFAULT_MAX_MCP_OUTPUT_TOKENS = 25000

# Rough char-to-token ratio (TS: charsPerToken ≈ 4)
_CHARS_PER_TOKEN = 4

# Content block type literals used by the MCP protocol
_BLOCK_TYPE_TEXT = "text"
_BLOCK_TYPE_IMAGE = "image"
_BLOCK_TYPE_RESOURCE = "resource"
_BLOCK_TYPE_TOOL_RESULT = "tool_result"
_BLOCK_TYPE_TOOL_USE = "tool_use"


# ---------------------------------------------------------------------------
# Feature-flag helper (GrowthBook parity)
# ---------------------------------------------------------------------------


def get_feature_value_cached_may_be_stale(_k: str, default: Any) -> Any:
    """Return a cached feature-flag value — tries GrowthBook, falls back to *default*.

    Matches the TS semantic: non-blocking read that may return a stale value (from
    disk cache), which is acceptable for configuration knobs like MCP output limits.
    """
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale as _gb_get,
        )

        return _gb_get(_k, default)
    except ImportError:
        return default


# ---------------------------------------------------------------------------
# Token-cap resolution
# ---------------------------------------------------------------------------


def get_max_mcp_output_tokens() -> int:
    """Resolve the MCP output token cap.

    Precedence (same as TS):
      1. ``MAX_MCP_OUTPUT_TOKENS`` env var (explicit user override)
      2. ``tengu_satin_quoll`` GrowthBook flag's ``mcp_tool`` key
         (tokens, not chars — unlike the other keys in that map which
         ``getPersistenceThreshold`` reads as chars; MCP has its own
         truncation layer upstream of that)
      3. Hard-coded ``DEFAULT_MAX_MCP_OUTPUT_TOKENS``
    """
    raw = os.environ.get("MAX_MCP_OUTPUT_TOKENS")
    if raw:
        try:
            v = int(raw, 10)
            if v > 0:
                return v
        except ValueError:
            pass

    overrides = (
        get_feature_value_cached_may_be_stale("tengu_satin_quoll", {}) or {}
    )
    o = overrides.get("mcp_tool") if isinstance(overrides, dict) else None
    if isinstance(o, (int, float)) and o > 0:
        return int(o)
    return DEFAULT_MAX_MCP_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# Content-type guards (mirror TS isTextBlock / isImageBlock helpers)
# ---------------------------------------------------------------------------


def _is_text_block(block: Any) -> bool:
    """Return whether *block* is an MCP text content block."""
    return isinstance(block, dict) and block.get("type") == _BLOCK_TYPE_TEXT


def _is_image_block(block: Any) -> bool:
    """Return whether *block* is an MCP image content block."""
    return isinstance(block, dict) and block.get("type") == _BLOCK_TYPE_IMAGE


def _is_resource_block(block: Any) -> bool:
    """Return whether *block* is an MCP resource content block."""
    return isinstance(block, dict) and block.get("type") == _BLOCK_TYPE_RESOURCE


def _is_tool_result_block(block: Any) -> bool:
    """Return whether *block* is an MCP tool-result content block."""
    return isinstance(block, dict) and block.get("type") == _BLOCK_TYPE_TOOL_RESULT


def _is_dict_block(block: Any) -> bool:
    """Return whether *block* looks like a content block dict."""
    return isinstance(block, dict) and "type" in block


# ---------------------------------------------------------------------------
# Size estimation
# ---------------------------------------------------------------------------


def get_content_size_estimate(content: str | list[dict[str, Any]] | None) -> int:
    """Estimate token count for MCP content without calling the API.

    Uses the rough ``CHARS_PER_TOKEN ≈ 4`` heuristic for text blocks and a
    fixed ``IMAGE_TOKEN_ESTIMATE`` for image blocks.  Returns 0 for falsy /
    empty content.
    """
    if not content:
        return 0
    if isinstance(content, str):
        return rough_token_count_estimation(content)

    total = 0
    for block in content:
        if not _is_dict_block(block):
            # Non-dict items: serialize to string for a rough estimate
            total += rough_token_count_estimation(str(block))
            continue
        if _is_text_block(block):
            total += rough_token_count_estimation(str(block.get("text", "")))
        elif _is_image_block(block):
            total += IMAGE_TOKEN_ESTIMATE
        elif _is_resource_block(block):
            # Resource blocks carry a ``resource`` sub-object; count text in it.
            resource = block.get("resource", {})
            if isinstance(resource, dict):
                total += rough_token_count_estimation(str(resource.get("text", "")))
            else:
                total += rough_token_count_estimation(str(resource))
        elif _is_tool_result_block(block):
            # Recurse into nested content array if present.
            nested = block.get("content")
            total += get_content_size_estimate(nested)  # type: ignore[arg-type]
        else:
            # Unknown block type — still count its serialised footprint.
            total += rough_token_count_estimation(str(block))
    return total


def get_content_size_chars(content: str | list[dict[str, Any]] | None) -> int:
    """Char-level size (not token estimate) of *content*.

    Useful for comparing against ``_max_chars()`` directly.
    """
    if not content:
        return 0
    if isinstance(content, str):
        return len(content)
    total = 0
    for block in content:
        if not isinstance(block, dict):
            total += len(str(block))
        elif _is_text_block(block):
            total += len(str(block.get("text", "")))
        elif _is_image_block(block):
            total += IMAGE_TOKEN_ESTIMATE * _CHARS_PER_TOKEN
        elif _is_resource_block(block):
            resource = block.get("resource", {})
            if isinstance(resource, dict):
                total += len(str(resource.get("text", "")))
            else:
                total += len(str(resource))
        elif _is_tool_result_block(block):
            nested = block.get("content")
            total += get_content_size_chars(nested)
        else:
            total += len(str(block))
    return total


# ---------------------------------------------------------------------------
# API-based precise token counting
# ---------------------------------------------------------------------------

# Module-level flag: set to True once we detect that the Anthropic SDK is
# installed and functional.  Avoids repeated ImportError churn on every call.
_anthropic_sdk_available: bool | None = None


def _check_anthropic_sdk() -> bool:
    """Test whether the Anthropic SDK can be imported."""
    global _anthropic_sdk_available
    if _anthropic_sdk_available is None:
        try:
            import anthropic  # noqa: F401
            _anthropic_sdk_available = True
        except ImportError:
            _anthropic_sdk_available = False
    return _anthropic_sdk_available


async def count_messages_tokens_with_api(
    _messages: list[Any], _tools: list[Any]
) -> int | None:
    """Count tokens for *messages* precisely via the API.

    Tries, in order:
      1. Anthropic Python SDK ``anthropic.beta.messages.count_tokens``
      2. ``tiktoken`` (OpenAI tokenizer — reasonable approximation)
      3. Falls back to ``None`` (caller uses the rough heuristic).

    When the API call fails we log the error and return ``None`` rather than
    raising, so the caller degrades gracefully to the rough-estimate path.
    """
    # --- path 1: Anthropic SDK -------------------------------------------------
    if _check_anthropic_sdk():
        try:
            from anthropic import Anthropic

            client = Anthropic(
                api_key=os.environ.get(
                    "ANTHROPIC_API_KEY",
                    os.environ.get("CLAUDE_API_KEY", ""),
                )
                or "sk-ant-placeholder",
            )
            response = await client.beta.messages.count_tokens(
                model="claude-sonnet-4-20250514",
                messages=_messages,
                tools=_tools,
            )
            if isinstance(response.input_tokens, int):
                return response.input_tokens
        except Exception as exc:
            log_error(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))

    # --- path 2: tiktoken ------------------------------------------------------
    try:
        import tiktoken

        # cl100k_base is a reasonable approximation for Claude token counts.
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for msg in _messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            total += len(enc.encode(str(block.get("text", ""))))
                        else:
                            total += len(enc.encode(str(block)))
                    else:
                        total += len(enc.encode(str(block)))
            total += 4  # per-message overhead
        return total
    except (ImportError, Exception):
        pass

    # --- path 3: nothing available --------------------------------------------
    return None


# ---------------------------------------------------------------------------
# Truncation helpers
# ---------------------------------------------------------------------------


def get_truncation_message() -> str:
    """Build the human-readable truncation notice.

    The message includes the effective token cap so the model can reason about
    what was dropped and decide whether to paginate / filter.
    """
    cap = get_max_mcp_output_tokens()
    return (
        f"\n\n[OUTPUT TRUNCATED - exceeded {cap} token limit]\n\n"
        "The tool output was truncated. "
        "If this MCP server provides pagination or filtering tools, "
        "use them to retrieve specific portions of the data. "
        "If pagination is not available, inform the user that you are "
        "working with truncated output and results may be incomplete."
    )


def _max_chars() -> int:
    """Maximum char footprint before truncation kicks in."""
    return get_max_mcp_output_tokens() * _CHARS_PER_TOKEN


def _truncate_string(content: str, max_chars: int) -> str:
    """Truncate *content* to *max_chars*, keeping multi-byte boundaries intact.

    When the cutoff falls in the middle of a multi-byte sequence we walk
    backwards to the nearest valid character start.  This avoids producing
    invalid Unicode that would break downstream consumers.
    """
    if len(content) <= max_chars:
        return content

    truncated = content[:max_chars]
    # Walk back to a valid UTF-8 boundary if we snipped a multi-byte char.
    try:
        truncated.encode("utf-8")
    except UnicodeEncodeError:
        # The last code-point is incomplete — strip it.
        for back in range(1, 5):
            candidate = truncated[:-back]
            try:
                candidate.encode("utf-8")
                truncated = candidate
                break
            except UnicodeEncodeError:
                continue
    return truncated


# ---------------------------------------------------------------------------
# Image compression
# ---------------------------------------------------------------------------


async def _compress_image_block(
    block: dict[str, Any],
    max_bytes: int,
) -> dict[str, Any] | None:
    """Attempt to compress an image content block to fit within *max_bytes*.

    Returns the compressed block on success, or ``None`` if compression is
    impossible or the image cannot be shrunk enough.

    Strategy:
      1. Decode base-64 data to raw bytes.
      2. If already under *max_bytes*, return as-is (no-op).
      3. Try Pillow to resize + re-encode at lower quality / smaller dimensions.
      4. If Pillow is unavailable, return ``None`` (caller will skip the image).

    This mirrors the TS ``compressImageBlock`` path that calls into
    ``compressImageBuffer`` for progressive resizing.
    """
    if not _is_image_block(block):
        return None

    source = block.get("source", {})
    if not isinstance(source, dict) or source.get("type") != "base64":
        # Non-base64 images (URL-based) cannot be compressed locally.
        return block

    data_b64: str = source.get("data", "")
    if not data_b64:
        return None

    import base64

    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        return None

    if len(raw) <= max_bytes:
        return block  # already fits

    # --- Pillow-based compression -----------------------------------------
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(raw))
        media_type = source.get("media_type", "image/png")

        # Progressive downscale: try 100 % → 75 % → 50 % → 25 %
        for scale in (1.0, 0.75, 0.5, 0.25):
            w = max(1, int(img.width * scale))
            h = max(1, int(img.height * scale))
            resized = img.resize((w, h), Image.LANCZOS)

            # Re-encode at reduced quality (JPEG) or compressed PNG.
            buf = io.BytesIO()
            if "png" in media_type:
                resized.save(buf, format="PNG", optimize=True)
            else:
                # Convert to RGB for JPEG (removes alpha channel if present).
                if resized.mode in ("RGBA", "P"):
                    resized = resized.convert("RGB")
                resized.save(buf, format="JPEG", quality=60, optimize=True)

            compressed_bytes = buf.getvalue()
            if len(compressed_bytes) <= max_bytes:
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg" if "png" not in media_type else media_type,
                        "data": base64.b64encode(compressed_bytes).decode("ascii"),
                    },
                }
    except ImportError:
        pass
    except Exception as exc:
        log_error(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))

    return None  # compression failed or made no progress


# ---------------------------------------------------------------------------
# Truncation predicates
# ---------------------------------------------------------------------------


async def mcp_content_needs_truncation(
    content: str | list[dict[str, Any]] | None,
) -> bool:
    """Return ``True`` when *content* likely exceeds the MCP output token cap.

    Two-stage check:
      1. Cheap heuristic: if ``get_content_size_estimate`` is within
         ``MCP_TOKEN_COUNT_THRESHOLD_FACTOR`` of the cap, assume safe.
      2. If the heuristic warns, attempt a precise token count via the API.
         Only truncate when the precise count confirms the overflow.

    Errors in the precise-count path are logged and treated as "no truncation
    needed" (fail-open — let the content through).
    """
    if not content:
        return False

    est = get_content_size_estimate(content)
    if est <= get_max_mcp_output_tokens() * MCP_TOKEN_COUNT_THRESHOLD_FACTOR:
        return False

    try:
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        tc = await count_messages_tokens_with_api(messages, [])
        return bool(tc and tc > get_max_mcp_output_tokens())
    except Exception as exc:
        log_error(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
        # On error, use the rough estimate directly as a fallback.
        # If the rough estimate is 2× the cap, it's almost certainly too large.
        if est > get_max_mcp_output_tokens() * 2:
            return True
        return False


# ---------------------------------------------------------------------------
# Core truncation logic
# ---------------------------------------------------------------------------


async def truncate_mcp_content_blocks(
    blocks: list[dict[str, Any]],
    max_chars: int,
) -> list[dict[str, Any]]:
    """Truncate a list of MCP content blocks to fit within *max_chars* chars.

    Rules (preserving TS semantics):
      - Text blocks: included until the budget is exhausted; the last block
        may be sliced.
      - Image blocks: included if they fit; if not, compression is attempted.
        If compression also fails, the image is dropped.
      - Resource / tool_result / other blocks: passed through and counted
        (they are not truncated individually).

    Returns a **new** list — does not mutate the input.
    """
    result: list[dict[str, Any]] = []
    current_chars = 0

    for block in blocks:
        if not isinstance(block, dict):
            # Non-dict items are serialized and treated as small.
            text_repr = str(block)
            remaining = max_chars - current_chars
            if remaining <= 0:
                break
            if len(text_repr) <= remaining:
                result.append(block)
                current_chars += len(text_repr)
            else:
                result.append(text_repr[:remaining])
                break
            continue

        if _is_text_block(block):
            text = str(block.get("text", ""))
            remaining = max_chars - current_chars
            if remaining <= 0:
                break
            if len(text) <= remaining:
                result.append(block)
                current_chars += len(text)
            else:
                result.append({"type": "text", "text": text[:remaining]})
                break

        elif _is_image_block(block):
            image_chars = IMAGE_TOKEN_ESTIMATE * _CHARS_PER_TOKEN
            if current_chars + image_chars <= max_chars:
                result.append(block)
                current_chars += image_chars
            else:
                # Image exceeds remaining budget — try compression.
                remaining = max_chars - current_chars
                if remaining > 0:
                    max_image_bytes = int(remaining * 0.75)  # base64 overhead
                    try:
                        compressed = await _compress_image_block(block, max_image_bytes)
                        if compressed is not None:
                            result.append(compressed)
                            source = compressed.get("source", {})
                            if isinstance(source, dict) and source.get("type") == "base64":
                                data: str = source.get("data", "")
                                current_chars += len(data)
                            else:
                                current_chars += image_chars
                    except Exception as exc:
                        log_error(
                            exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                        )
                # else: budget is fully consumed — drop the image.

        elif _is_resource_block(block):
            # Preserve resource blocks — their size is hard to estimate
            # accurately, but they are typically small (URIs + metadata).
            resource = block.get("resource", {})
            resource_chars = (
                len(str(resource.get("text", "")))
                if isinstance(resource, dict)
                else len(str(resource))
            )
            if current_chars + resource_chars <= max_chars:
                result.append(block)
                current_chars += resource_chars
            else:
                # Resource doesn't fit; skip it rather than corrupt it.
                pass

        elif _is_tool_result_block(block):
            # Nested content — truncate recursively.
            nested = block.get("content")
            remaining = max_chars - current_chars
            if remaining <= 0:
                break
            if isinstance(nested, list):
                truncated_nested = await truncate_mcp_content_blocks(
                    nested, remaining
                )
                result.append({**block, "content": truncated_nested})
                current_chars += sum(
                    len(str(b.get("text", ""))) if isinstance(b, dict) else len(str(b))
                    for b in truncated_nested
                )
            else:
                result.append(block)
                current_chars += len(str(nested)) if nested else 0

        else:
            # Unknown block type — preserve as-is.
            block_chars = len(str(block))
            if current_chars + block_chars <= max_chars:
                result.append(block)
                current_chars += block_chars
            else:
                break

    return result


async def truncate_mcp_content(
    content: str | list[dict[str, Any]] | None,
) -> str | list[dict[str, Any]] | None:
    """Truncate MCP tool result content to fit within the token cap.

    - Strings: truncated at ``_max_chars()`` with the truncation message appended.
    - Lists of content blocks: each block is processed according to its type
      (see ``truncate_mcp_content_blocks``); the truncation message is appended
      as a final ``text`` block.
    - ``None`` / falsy: returned unchanged.

    Errors inside block processing are caught and logged; the function returns
    the partially-truncated result rather than failing entirely.
    """
    if not content:
        return content

    mc = _max_chars()
    msg = get_truncation_message()

    try:
        if isinstance(content, str):
            return _truncate_string(content, mc) + msg

        if isinstance(content, list):
            truncated = await truncate_mcp_content_blocks(content, mc)
            truncated.append({"type": "text", "text": msg})
            return truncated

        # Defensive: unknown content type — convert to string and truncate.
        return _truncate_string(str(content), mc) + msg

    except Exception as exc:
        log_error(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
        # Best-effort return: the original content (may be oversized but won't
        # crash the conversation).
        return content


async def truncate_mcp_content_if_needed(
    content: str | list[dict[str, Any]] | None,
) -> str | list[dict[str, Any]] | None:
    """Conditionally truncate *content* if it exceeds the MCP output token cap.

    This is the primary entry-point used by MCP tool execution
    (``hare/tools_impl/MCPTool/mcp_tool.py``).
    """
    if not await mcp_content_needs_truncation(content):
        return content
    return await truncate_mcp_content(content)


# ---------------------------------------------------------------------------
# Convenience / introspection helpers
# ---------------------------------------------------------------------------


def get_mcp_token_budget() -> dict[str, int]:
    """Return the current MCP token budget as a human-readable dict.

    Useful for debugging and logging.
    """
    cap = get_max_mcp_output_tokens()
    return {
        "max_output_tokens": cap,
        "chars_per_token": _CHARS_PER_TOKEN,
        "max_output_chars": cap * _CHARS_PER_TOKEN,
        "threshold_factor": MCP_TOKEN_COUNT_THRESHOLD_FACTOR,
        "threshold_tokens": int(cap * MCP_TOKEN_COUNT_THRESHOLD_FACTOR),
        "image_token_estimate": IMAGE_TOKEN_ESTIMATE,
    }


def estimate_mcp_result_tokens(result: str | list[dict[str, Any]] | None) -> int:
    """Convenience: quick token-estimate for an MCP tool result (no API call)."""
    return get_content_size_estimate(result)


def would_mcp_result_be_truncated(
    result: str | list[dict[str, Any]] | None,
) -> bool:
    """Synchronous check: would this result likely need truncation?

    Uses only the rough heuristic (no API call), so it may produce false
    positives / negatives.  For the real check use the async
    ``mcp_content_needs_truncation``.
    """
    if not result:
        return False
    est = get_content_size_estimate(result)
    return est > get_max_mcp_output_tokens() * MCP_TOKEN_COUNT_THRESHOLD_FACTOR


__all__ = [
    "MCP_TOKEN_COUNT_THRESHOLD_FACTOR",
    "IMAGE_TOKEN_ESTIMATE",
    "DEFAULT_MAX_MCP_OUTPUT_TOKENS",
    "get_feature_value_cached_may_be_stale",
    "get_max_mcp_output_tokens",
    "get_content_size_estimate",
    "get_content_size_chars",
    "count_messages_tokens_with_api",
    "get_truncation_message",
    "truncate_mcp_content",
    "truncate_mcp_content_blocks",
    "truncate_mcp_content_if_needed",
    "mcp_content_needs_truncation",
    "get_mcp_token_budget",
    "estimate_mcp_result_tokens",
    "would_mcp_result_be_truncated",
]
