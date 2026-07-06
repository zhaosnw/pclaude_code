"""
MCP tool/server name parsing, sanitization, truncation, and display helpers.

Port of: src/services/mcp/mcpStringUtils.ts

Adds name sanitization (MCP naming pattern ^[a-zA-Z0-9_-]{1,64}$),
truncation for UI display, safe encoding/escaping for JSON and transport,
and validation utilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from logging import getLogger
from typing import Optional

from hare.services.mcp.normalization import normalize_name_for_mcp

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP naming constants
# ---------------------------------------------------------------------------

MCP_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MCP_ILLEGAL_CHARS_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")
MCP_PREFIX = "mcp__"
MCP_PREFIX_SEPARATOR = "__"
DEFAULT_MAX_NAME_LENGTH = 64
DEFAULT_MAX_DESCRIPTION_LENGTH = 1024
DISPLAY_NAME_SEPARATOR = " - "
MCP_SUFFIX = "(MCP)"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class McpInfo:
    server_name: str
    tool_name: Optional[str] = None


@dataclass
class McpDisplayInfo:
    """Display-ready representation of an MCP tool/server name."""

    full_name: str
    display_name: str
    server_name: str
    tool_name: Optional[str] = None
    is_mcp: bool = True
    truncated: bool = False


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


def is_valid_mcp_name(name: str) -> bool:
    """Check if a name conforms to the MCP naming pattern ^[a-zA-Z0-9_-]{1,64}$.

    Returns True if the name is valid for use as an MCP server or tool name.
    """
    if not name or not isinstance(name, str):
        return False
    return bool(MCP_NAME_PATTERN.match(name))


def validate_mcp_name(name: str, label: str = "name") -> tuple[bool, Optional[str]]:
    """Validate an MCP name and return (is_valid, error_message).

    Args:
        name: The name to validate.
        label: Human-readable label for error messages (e.g. "server name").

    Returns:
        Tuple of (is_valid, error_message_or_None).
    """
    if not name:
        return False, f"{label} must not be empty"
    if not isinstance(name, str):
        return False, f"{label} must be a string, got {type(name).__name__}"
    if len(name) > 64:
        return False, f"{label} must be at most 64 characters, got {len(name)}"
    if not MCP_NAME_PATTERN.match(name):
        return False, (
            f"{label} contains invalid characters. "
            f"Only alphanumeric (a-z, A-Z, 0-9), underscores (_), "
            f"and hyphens (-) are allowed."
        )
    return True, None


# ---------------------------------------------------------------------------
# Name sanitization
# ---------------------------------------------------------------------------


def sanitize_mcp_name(name: str, max_length: int = DEFAULT_MAX_NAME_LENGTH) -> str:
    """Sanitize a string to conform to the MCP naming pattern.

    Replaces illegal characters with underscores, collapses consecutive
    underscores, strips leading/trailing underscores, and truncates to
    max_length. Always returns a valid MCP name or raises ValueError.

    Args:
        name: The raw name to sanitize.
        max_length: Maximum length for the result (default 64).

    Returns:
        A sanitized name matching ^[a-zA-Z0-9_-]{1,max_length}$.

    Raises:
        ValueError: If the result would be empty after sanitization.
    """
    if not name:
        raise ValueError("Cannot sanitize an empty name")

    # Replace illegal characters with underscores
    sanitized = MCP_ILLEGAL_CHARS_PATTERN.sub("_", name)

    # Collapse consecutive underscores
    sanitized = re.sub(r"_+", "_", sanitized)

    # Strip leading and trailing separators
    sanitized = sanitized.strip("_-")

    # Truncate to max_length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip("_-")

    # Ensure the result is not empty
    if not sanitized:
        raise ValueError(
            f"Sanitization of '{name}' produced an empty string "
            f"(all characters were illegal or stripped)"
        )

    return sanitized


def sanitize_mcp_description(
    description: str, max_length: int = DEFAULT_MAX_DESCRIPTION_LENGTH
) -> str:
    """Sanitize an MCP tool description for safe transport and storage.

    Strips leading/trailing whitespace, collapses excessive whitespace,
    removes null bytes and control characters (except newlines), and
    truncates to max_length at a word boundary when possible.

    Args:
        description: Raw description text.
        max_length: Maximum length (default 1024).

    Returns:
        Cleaned and potentially truncated description string.
    """
    if not description:
        return ""

    # Strip outer whitespace
    cleaned = description.strip()

    # Remove null bytes and non-printable control characters (except \n, \r, \t)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)

    # Collapse multiple spaces and tabs (but preserve newlines)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)

    # Collapse 3+ consecutive newlines into at most 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    if len(cleaned) <= max_length:
        return cleaned

    # Truncate at word boundary when possible
    truncated = cleaned[:max_length]
    last_space = truncated.rfind(" ")
    last_newline = truncated.rfind("\n")
    cut_point = max(last_space, last_newline, max_length - 3)

    truncated = cleaned[:cut_point].rstrip()
    return f"{truncated}..."


# ---------------------------------------------------------------------------
# Name truncation for UI display
# ---------------------------------------------------------------------------


def truncate_mcp_name(
    name: str,
    max_length: int = 32,
    ellipsis: str = "…",
    preserve_suffix: bool = False,
) -> str:
    """Truncate an MCP name for compact UI display.

    Args:
        name: The name to truncate.
        max_length: Maximum display length (including ellipsis).
        ellipsis: String to append when truncation occurs.
        preserve_suffix: If True, attempt to keep the last segment (after last
            separator) intact, truncating the middle portion.

    Returns:
        The (possibly truncated) display string.
    """
    if len(name) <= max_length:
        return name

    available = max_length - len(ellipsis)
    if available <= 0:
        return name[:max_length]

    if not preserve_suffix:
        return name[:available] + ellipsis

    # Find the last meaningful separator
    for sep in ("__", "_", "-", "."):
        idx = name.rfind(sep)
        if idx > 0:
            suffix = name[idx:]  # includes separator
            prefix_available = available - len(suffix)
            if prefix_available > 2:
                return name[:prefix_available] + ellipsis + suffix
            break

    # Fallback: simple head truncation
    return name[:available] + ellipsis


def shorten_mcp_tool_name(full_name: str, max_len: int = 80) -> str:
    """Shorten a full MCP tool name (mcp__server__tool) for UI display.

    Preserves the mcp__ prefix and the tool name portion, abbreviating the
    server name in the middle if necessary.

    Args:
        full_name: Full MCP tool name (e.g., "mcp__my_server__my_tool").
        max_len: Maximum display length.

    Returns:
        Shortened name suitable for compact UI contexts.
    """
    if len(full_name) <= max_len:
        return full_name

    info = mcp_info_from_string(full_name)
    if info is None:
        return truncate_mcp_name(full_name, max_len)

    server = info.server_name
    tool = info.tool_name or ""

    # Always keep "mcp__" prefix and "__toolname" suffix
    prefix = MCP_PREFIX
    suffix = f"__{tool}" if tool else ""

    available_for_server = max_len - len(prefix) - len(suffix)
    if available_for_server < 4:
        # Not enough room, do simple head truncation of whole string
        return truncate_mcp_name(full_name, max_len)

    return f"{prefix}{truncate_mcp_name(server, available_for_server)}{suffix}"


def truncate_mcp_output(output: str, max_chars: int = 100_000) -> str:
    """Truncate MCP tool output with a human-readable notice.

    Args:
        output: Raw tool output string.
        max_chars: Maximum characters before truncation.

    Returns:
        Original string or truncated string with notice.
    """
    if len(output) <= max_chars:
        return output
    truncated = output[:max_chars]
    return f"{truncated}\n\n[Output truncated at {max_chars:,} characters]"


# ---------------------------------------------------------------------------
# Safe encoding / escaping
# ---------------------------------------------------------------------------


def escape_mcp_string(value: str, *, for_json: bool = True) -> str:
    """Escape an MCP-related string for safe transport.

    Handles escaping for JSON embedding (default) or general text safety.
    Removes or replaces characters that could cause parsing issues.

    Args:
        value: The string to escape.
        for_json: If True, escape for JSON string embedding.

    Returns:
        Escaped string.
    """
    if not value:
        return ""

    if for_json:
        # Use Python's unicode_escape for full coverage, then ensure
        # it's safe for JSON (no raw newlines, tabs, or unescaped quotes)
        escaped = value.encode("unicode_escape").decode("ascii", errors="replace")
        # Restore safe printable characters that unicode_escape over-escapes
        # (unicode_escape escapes everything non-ASCII; we only want structural chars)
        # Instead, do a targeted escape
        result = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace("\b", "\\b")
            .replace("\f", "\\f")
        )
        return result

    # General text safety: remove null bytes and surrogate ranges
    return value.replace("\x00", "")


def unescape_mcp_string(escaped: str) -> str:
    """Reverse escape_mcp_string (JSON-targeted escaping).

    Args:
        escaped: A string previously escaped with escape_mcp_string.

    Returns:
        Unescaped string.
    """
    if not escaped:
        return ""

    result = (
        escaped.replace("\\t", "\t")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\b", "\b")
        .replace("\\f", "\f")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )
    return result


def safe_encode_for_url(value: str) -> str:
    """Percent-encode a string for safe inclusion in MCP URLs.

    Uses UTF-8 encoding with percent-encoding for non-ASCII and
    special URL characters. Safe characters (unreserved per RFC 3986)
    are left as-is.

    Args:
        value: The string to encode.

    Returns:
        URL-safe encoded string.
    """
    from urllib.parse import quote

    return quote(value, safe="-_.~")


def safe_decode_from_url(encoded: str) -> str:
    """Percent-decode a URL-encoded string.

    Args:
        encoded: A percent-encoded string.

    Returns:
        Decoded string, or the original if decoding fails.
    """
    from urllib.parse import unquote

    try:
        return unquote(encoded)
    except Exception:
        logger.warning("Failed to URL-decode string: %s", encoded, exc_info=True)
        return encoded


# ---------------------------------------------------------------------------
# Original parsing and building functions (preserved)
# ---------------------------------------------------------------------------


def mcp_info_from_string(tool_string: str) -> McpInfo | None:
    """Parse an MCP tool string like 'mcp__server__tool__subtool' into parts.

    Returns McpInfo if the string is a valid MCP tool reference, None otherwise.
    """
    parts = tool_string.split("__")
    if len(parts) < 2:
        return None
    mcp_part = parts[0]
    server_name = parts[1]
    tool_name_parts = parts[2:]
    if mcp_part != "mcp" or not server_name:
        return None
    tool_name = "__".join(tool_name_parts) if tool_name_parts else None
    return McpInfo(server_name=server_name, tool_name=tool_name)


def get_mcp_prefix(server_name: str) -> str:
    """Build the MCP prefix for a server: 'mcp__{normalized_server}__'."""
    return f"{MCP_PREFIX}{normalize_name_for_mcp(server_name)}__"


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build a full MCP tool name: 'mcp__{server}__{tool}' with normalization."""
    return f"{get_mcp_prefix(server_name)}{normalize_name_for_mcp(tool_name)}"


def get_tool_name_for_permission_check(tool: object) -> str:
    """Extract the canonical MCP tool name from a tool object for permission checks.

    Handles both dataclass-style tools (with mcp_info attribute) and dict-style
    tools (with mcp_info dict key).
    """
    name = getattr(tool, "name", "")
    mcp_info = getattr(tool, "mcp_info", None)
    if mcp_info is not None:
        sn = getattr(mcp_info, "server_name", None) or mcp_info.get("serverName")  # type: ignore[union-attr]
        tn = getattr(mcp_info, "tool_name", None) or mcp_info.get("toolName")  # type: ignore[union-attr]
        if sn and tn:
            return build_mcp_tool_name(str(sn), str(tn))
    return str(name)


def get_mcp_display_name(full_name: str, server_name: str) -> str:
    """Strip the MCP prefix from a full tool name to get a user-facing display name.

    Example:
        'mcp__github__search_repos' with server 'github' → 'search_repos'
    """
    prefix = f"{MCP_PREFIX}{normalize_name_for_mcp(server_name)}__"
    return full_name.replace(prefix, "", 1)


def extract_mcp_tool_display_name(user_facing_name: str) -> str:
    """Extract a clean tool display name from a user-facing MCP tool string.

    Handles strings like 'Server Name - Tool Name (MCP)' → 'Tool Name'.
    """
    without_suffix = user_facing_name.replace(MCP_SUFFIX, "").strip()
    idx = without_suffix.find(DISPLAY_NAME_SEPARATOR)
    if idx != -1:
        return without_suffix[idx + len(DISPLAY_NAME_SEPARATOR) :].strip()
    return without_suffix


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------


def format_mcp_tool_display(full_name: str, *, max_len: int = 0) -> McpDisplayInfo:
    """Produce a full display info struct from a raw MCP tool name.

    Handles parsing, normalization, and optional truncation in one call.

    Args:
        full_name: The full MCP tool name (e.g. 'mcp__server__tool').
        max_len: If > 0, truncate display_name to this length with ellipsis.

    Returns:
        McpDisplayInfo with all display fields populated.
    """
    info = mcp_info_from_string(full_name)
    if info is None:
        display = full_name
        if max_len > 0 and len(display) > max_len:
            display = truncate_mcp_name(display, max_len)
            return McpDisplayInfo(
                full_name=full_name,
                display_name=display,
                server_name="",
                tool_name=None,
                is_mcp=False,
                truncated=True,
            )
        return McpDisplayInfo(
            full_name=full_name,
            display_name=display,
            server_name="",
            tool_name=None,
            is_mcp=False,
        )

    tool_display = info.tool_name if info.tool_name else ""
    display = tool_display if tool_display else info.server_name
    truncated = False

    if max_len > 0 and len(display) > max_len:
        display = truncate_mcp_name(display, max_len)
        truncated = True

    return McpDisplayInfo(
        full_name=full_name,
        display_name=display,
        server_name=info.server_name,
        tool_name=info.tool_name,
        is_mcp=True,
        truncated=truncated,
    )


def build_user_facing_mcp_name(server_name: str, tool_name: str) -> str:
    """Build a user-facing name like 'Server Name - Tool Name (MCP)'.

    Args:
        server_name: Raw MCP server name.
        tool_name: Raw MCP tool name.

    Returns:
        User-facing display string.
    """
    display_server = normalize_name_for_mcp(server_name).replace("_", " ").title()
    display_tool = normalize_name_for_mcp(tool_name).replace("_", " ").title()
    return f"{display_server}{DISPLAY_NAME_SEPARATOR}{display_tool} {MCP_SUFFIX}"


def is_mcp_tool_name(name: str) -> bool:
    """Check if a string is an MCP tool name (starts with 'mcp__').

    More robust than a simple startswith — validates the prefix separator
    and that a server name follows.
    """
    if not name.startswith(MCP_PREFIX):
        return False
    # Must have at least "mcp__X" where X is non-empty
    remainder = name[len(MCP_PREFIX) :]
    return MCP_PREFIX_SEPARATOR in remainder and len(remainder.split(MCP_PREFIX_SEPARATOR, 1)[0]) > 0


# ---------------------------------------------------------------------------
# Error message formatting
# ---------------------------------------------------------------------------


def format_mcp_error_for_user(error: Exception | str) -> str:
    """Format an MCP-related error into a user-friendly message.

    Strips internal stack traces and JSON-RPC noise, returning a concise
    message suitable for display to the end user.

    Args:
        error: An Exception instance or error string.

    Returns:
        User-friendly error string.
    """
    if isinstance(error, str):
        msg = error
    else:
        msg = str(error) or error.__class__.__name__

    # Strip JSON-RPC error prefixes that leak implementation details
    msg = re.sub(r"^MCP error -?\d*:?\s*", "", msg)
    msg = re.sub(r"^\s*jsonrpc error\s*[-:]\s*", "", msg, flags=re.IGNORECASE)

    # Truncate overly long error messages
    if len(msg) > 500:
        msg = msg[:497] + "..."

    # Ensure the message is non-empty
    return msg.strip() or "An unknown MCP error occurred"


def build_mcp_tool_error_message(server_name: str, tool_name: str, error_msg: str) -> str:
    """Build a structured error message for a failed MCP tool call.

    Args:
        server_name: The MCP server name.
        tool_name: The tool that was called.
        error_msg: The underlying error message.

    Returns:
        Formatted error string.
    """
    safe_msg = format_mcp_error_for_user(error_msg)
    return (
        f"Error calling MCP tool '{tool_name}' on server "
        f"'{server_name}': {safe_msg}"
    )
