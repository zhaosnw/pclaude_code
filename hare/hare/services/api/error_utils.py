"""
TLS / connection error classification and API error formatting utilities.

Port of: src/services/api/errorUtils.ts (261 lines)

Provides:
- ConnectionErrorDetails dataclass
- extract_connection_error_details() — walks cause chain for SSL codes
- format_api_error() — SSL code-specific user-facing messages
- sanitize_message_html() — strip HTML from CloudFlare error pages
- extract_nested_error_message() — walk error nesting for API errors
"""

from __future__ import annotations

import errno
import re
from dataclasses import dataclass
from typing import Any, Optional


# ---------------------------------------------------------------------------
# SSL error codes (expanded from TS — 18 codes)
# ---------------------------------------------------------------------------

_SSL_CODES = frozenset(
    {
        "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
        "UNABLE_TO_GET_ISSUER_CERT",
        "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
        "CERT_HAS_EXPIRED",
        "CERT_SIGNATURE_FAILURE",
        "CERT_NOT_YET_VALID",
        "CERT_REVOKED",
        "CERT_REJECTED",
        "CERT_UNTRUSTED",
        "CERT_CHAIN_TOO_LONG",
        "PATH_LENGTH_EXCEEDED",
        "DEPTH_ZERO_SELF_SIGNED_CERT",
        "SELF_SIGNED_CERT_IN_CHAIN",
        "ERR_TLS_CERT_ALTNAME_INVALID",
        "HOSTNAME_MISMATCH",
        "ERR_TLS_HANDSHAKE_TIMEOUT",
        "ERR_SSL_WRONG_VERSION_NUMBER",
        "EPROTO",
    }
)

# Map Python errno codes to string equivalents for OSError
_ERRNO_TO_SSL_CODE: dict[int, str] = {
    errno.ECONNRESET: "ECONNRESET",
    errno.ECONNREFUSED: "ECONNREFUSED",
    errno.ETIMEDOUT: "ETIMEDOUT",
    errno.ENOTCONN: "ENOTCONN",
    errno.EHOSTUNREACH: "EHOSTUNREACH",
    errno.ENETUNREACH: "ENETUNREACH",
    errno.EPIPE: "EPIPE",
}

# SSL-related errno values (macOS/Linux may differ)
_SSL_ERRNO_VALUES: frozenset[int] = frozenset({
    -9800, -9801, -9802, -9803, -9804, -9805, -9806, -9807, -9808, -9809,  # macOS Security framework
    -9810, -9811, -9812, -9813, -9814, -9815, -9816, -9817, -9818, -9819,
    -9820, -9821, -9822, -9823, -9824, -9825, -9826, -9827, -9828, -9829,
    -9830, -9831, -9832, -9833, -9834, -9835, -9836, -9837, -9838, -9839,
    -9840, -9841, -9842, -9843, -9844, -9845, -9846, -9847, -9848, -9849,
    -9850, -9851, -9852, -9853, -9854, -9855, -9856, -9857, -9858, -9859,
    -9860, -9861, -9862, -9863, -9864, -9865, -9866, -9867, -9868, -9869,
})


# ---------------------------------------------------------------------------
# ConnectionErrorDetails
# ---------------------------------------------------------------------------


@dataclass
class ConnectionErrorDetails:
    code: str
    message: str
    is_ssl_error: bool


def extract_connection_error_details(
    error: BaseException | None,
) -> Optional[ConnectionErrorDetails]:
    """Walk the exception cause chain to find connection error details."""
    if error is None:
        return None
    depth = 0
    current: BaseException | None = error
    while current is not None and depth < 5:
        # Check for string code attribute
        code = getattr(current, "errno", None) or getattr(current, "code", None)
        if isinstance(code, str):
            return ConnectionErrorDetails(
                code=code,
                message=str(current),
                is_ssl_error=code in _SSL_CODES,
            )
        # Check for integer errno (OSError)
        if isinstance(code, int):
            code_str = _ERRNO_TO_SSL_CODE.get(code, str(code))
            return ConnectionErrorDetails(
                code=code_str,
                message=str(current),
                is_ssl_error=code_str in _SSL_CODES or code in _SSL_ERRNO_VALUES,
            )
        current = (
            current.__cause__ if isinstance(current.__cause__, BaseException) else None
        )
        depth += 1
    return None


# ---------------------------------------------------------------------------
# format_api_error — user-facing connection error messages
# ---------------------------------------------------------------------------


def format_api_error(error: Any) -> str:
    """Format an API connection error into a user-friendly message.

    Port of: TS formatAPIError (errorUtils.ts lines 200-260)
    """
    details = extract_connection_error_details(error)

    if details is None:
        # Fallback: check the error message for known patterns
        msg = str(error) if error else "Unknown error"
        msg_lower = msg.lower()

        if "timeout" in msg_lower:
            return "Request timed out. Check your internet connection and proxy settings."
        if "ssl" in msg_lower or "tls" in msg_lower or "certificate" in msg_lower:
            return "SSL connection error. Check your proxy or network settings."
        if "refused" in msg_lower or "econnrefused" in msg_lower:
            return "Connection refused. Please check your internet connection and proxy settings."
        if "unreachable" in msg_lower or "enotfound" in msg_lower:
            return "Cannot reach server. Please check your internet connection."
        return f"Connection error. {msg}"

    code = details.code

    # SSL-specific messages
    if code == "UNABLE_TO_VERIFY_LEAF_SIGNATURE":
        return (
            "SSL certificate verification failed. This may be caused by a corporate proxy "
            "or firewall intercepting HTTPS traffic. Try setting the NODE_EXTRA_CA_CERTS "
            "environment variable to your company's root CA certificate."
        )
    if code == "UNABLE_TO_GET_ISSUER_CERT" or code == "UNABLE_TO_GET_ISSUER_CERT_LOCALLY":
        return (
            "Unable to verify the SSL certificate issuer. This may be caused by a proxy "
            "or firewall. Check your network settings."
        )
    if code == "SELF_SIGNED_CERT_IN_CHAIN" or code == "DEPTH_ZERO_SELF_SIGNED_CERT":
        return (
            "The server's SSL certificate is self-signed. If you are behind a corporate proxy, "
            "you may need to configure your CA certificate."
        )
    if code == "CERT_HAS_EXPIRED" or code == "CERT_NOT_YET_VALID":
        return "The server's SSL certificate has expired or is not yet valid."
    if code == "CERT_REVOKED":
        return "The server's SSL certificate has been revoked."
    if code in ("ERR_TLS_CERT_ALTNAME_INVALID", "HOSTNAME_MISMATCH"):
        return (
            "The server's SSL certificate does not match the hostname. "
            "This may indicate a proxy or DNS misconfiguration."
        )

    # Connection errors
    if code == "ETIMEDOUT":
        return "Request timed out. Check your internet connection and proxy settings."
    if code in ("ECONNRESET", "ECONNREFUSED", "ENOTCONN"):
        return "Connection failed. Please check your internet connection and proxy settings."
    if code == "EPIPE":
        return "Connection was closed unexpectedly. Please try again."

    # SSL fallback
    if details.is_ssl_error:
        return f"SSL connection error ({code}). Check your network or proxy settings."

    # Generic
    return f"Connection error ({code}). Please check your network settings."


# ---------------------------------------------------------------------------
# sanitize_message_html — strip CloudFlare/HTML error pages
# ---------------------------------------------------------------------------


def sanitize_message_html(message: str) -> str:
    """Detect and strip HTML from API error messages.

    CloudFlare and other proxies may wrap API errors in HTML pages.
    Extract the meaningful content if possible.
    """
    if not message:
        return message
    lower = message.lower().strip()
    if not lower.startswith("<html") and not lower.startswith("<!doctype html"):
        return message
    # Try to extract <title> text
    title_match = re.search(r"<title>(.*?)</title>", message, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()
        if title:
            return title
    return ""


def sanitize_api_error(error_message: str) -> str:
    """Apply sanitize_message_html and return the cleaned message."""
    sanitized = sanitize_message_html(error_message)
    return sanitized if sanitized else error_message


# ---------------------------------------------------------------------------
# extract_nested_error_message — walk API error nesting
# ---------------------------------------------------------------------------


def extract_nested_error_message(error: Any) -> Optional[str]:
    """Extract the innermost error message from nested API error structures.

    Walks: error.error.error.message (Anthropic shape)
           error.error.message (Bedrock shape)
    """
    if not isinstance(error, dict):
        return None

    # Try Anthropic shape: error.error.error.message
    inner = error.get("error")
    if isinstance(inner, dict):
        inner2 = inner.get("error")
        if isinstance(inner2, dict):
            msg = inner2.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        # Try one level: error.error.message
        msg = inner.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()

    # Direct message
    msg = error.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()

    return None
