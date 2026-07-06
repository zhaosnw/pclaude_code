"""
Debug utilities for bridge logging — redaction, truncation, error description.

Port of: src/bridge/debugUtils.ts
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

DEBUG_MSG_LIMIT = 2000

_SECRET_FIELD_NAMES = [
    "session_ingress_token",
    "environment_secret",
    "access_token",
    "secret",
    "token",
]

_SECRET_PATTERN = re.compile(
    r'"(' + "|".join(_SECRET_FIELD_NAMES) + r')"\s*:\s*"([^"]*)"'
)
_REDACT_MIN_LENGTH = 16


def redact_secrets(s: str) -> str:
    """Redact known secret fields in JSON strings."""

    def _replacer(m: re.Match[str]) -> str:
        field = m.group(1)
        value = m.group(2)
        if len(value) < _REDACT_MIN_LENGTH:
            return f'"{field}":"[REDACTED]"'
        return f'"{field}":"{value[:8]}...{value[-4:]}"'

    return _SECRET_PATTERN.sub(_replacer, s)


def debug_truncate(s: str, max_len: int = DEBUG_MSG_LIMIT) -> str:
    """Truncate a string for debug logging, collapsing newlines."""
    flat = s.replace("\n", "\\n")
    if len(flat) <= max_len:
        return flat
    return f"{flat[:max_len]}... ({len(flat)} chars)"


def debug_body(data: Any, max_len: int = DEBUG_MSG_LIMIT) -> str:
    """Truncate a JSON-serializable value for debug logging."""
    raw = data if isinstance(data, str) else json.dumps(data)
    s = redact_secrets(raw)
    if len(s) <= max_len:
        return s
    return f"{s[:max_len]}... ({len(s)} chars)"


def describe_http_error(err: Any) -> str:
    """Extract a descriptive error message with server detail if available."""
    msg = str(err) if not isinstance(err, Exception) else str(err)
    if hasattr(err, "response") and hasattr(err.response, "data"):
        data = err.response.data
        if isinstance(data, dict):
            detail = data.get("message") or (data.get("error", {}) or {}).get("message")
            if detail:
                return f"{msg}: {detail}"
    return msg


def extract_http_status(err: Any) -> Optional[int]:
    """Extract HTTP status code from error, or None."""
    if hasattr(err, "response") and hasattr(err.response, "status"):
        status = err.response.status
        if isinstance(status, int):
            return status
    return None


def extract_error_detail(data: Any) -> Optional[str]:
    """Pull a human-readable message from API error response body."""
    if not data or not isinstance(data, dict):
        return None
    if "message" in data and isinstance(data["message"], str):
        return data["message"]
    err = data.get("error")
    if isinstance(err, dict) and "message" in err and isinstance(err["message"], str):
        return err["message"]
    return None


def log_bridge_skip(
    reason: str, debug_msg: Optional[str] = None, v2: Optional[bool] = None
) -> None:
    """Log a bridge init skip event."""
    if debug_msg:
        import logging

        logging.getLogger("bridge").debug(debug_msg)
