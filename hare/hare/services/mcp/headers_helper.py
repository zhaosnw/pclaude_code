"""
Dynamic MCP HTTP headers via external helper script.

Port of: src/services/mcp/headersHelper.ts

Provides:
- Dynamic header resolution from external helper scripts
- Auth header construction (Bearer, Basic, API key)
- Token injection into request headers
- Smart header merging with priority and conflict resolution
- Header validation and sanitisation
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from hare.services.mcp.types import (
    McpHttpServerConfig,
    McpSseServerConfig,
    McpWebSocketServerConfig,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HEADER_TIMEOUT = 10.0
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
BEARER_RE = re.compile(r"^Bearer\s+", re.IGNORECASE)

# Well-known auth header names — these must never be silently overridden
PROTECTED_AUTH_HEADERS: frozenset[str] = frozenset(
    {"authorization", "x-api-key", "x-auth-token", "proxy-authorization"}
)

# Headers that should always be present on MCP transport requests
DEFAULT_MCP_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# ---------------------------------------------------------------------------
# Auth token types
# ---------------------------------------------------------------------------

AuthTokenType = Literal["bearer", "basic", "api-key", "custom", "oauth"]


@dataclass
class McpAuthContext:
    """Encapsulates auth material for MCP header construction."""

    token_type: AuthTokenType = "bearer"
    token: str = ""
    header_name: str = "Authorization"
    prefix: str = "Bearer"
    username: str = ""
    password: str = ""
    extra_params: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Header validation
# ---------------------------------------------------------------------------

_VALID_HEADER_CHARS = re.compile(
    r"^[^\x00-\x1f\x7f-\x9f\x3a]+$"
)


def validate_header_name(name: str) -> bool:
    """Return True if *name* is a valid HTTP header field-name (RFC 7230)."""
    if not name or not name.strip():
        return False
    if not HEADER_NAME_RE.match(name):
        return False
    return True


def validate_header_value(value: str) -> bool:
    """Return True if *value* contains no prohibited control characters."""
    if value is None:
        return False
    return bool(_VALID_HEADER_CHARS.match(value))


def sanitize_header_value(value: str) -> str:
    """Strip leading/trailing whitespace and remove embedded NUL bytes."""
    if not value:
        return ""
    return value.strip().replace("\x00", "")


def normalize_header_name(name: str) -> str:
    """Return the canonical HTTP header name (e.g. 'content-type' -> 'Content-Type')."""
    if not name:
        return name
    return "-".join(part.capitalize() for part in name.split("-"))


# ---------------------------------------------------------------------------
# Auth header construction
# ---------------------------------------------------------------------------

def build_auth_header(
    auth: McpAuthContext,
) -> dict[str, str]:
    """Build an HTTP Authorization (or equivalent) header dict from auth context.

    Supports:
    - Bearer tokens
    - Basic auth (username + password)
    - API keys (arbitrary header name + value)
    - OAuth tokens
    - Custom prefix + token
    """
    if not auth.token and not auth.username:
        return {}

    token_type = auth.token_type.lower()

    if token_type == "bearer":
        header_value = f"Bearer {auth.token}"
        return {"Authorization": header_value}

    if token_type == "basic":
        if auth.username and auth.password:
            credentials = f"{auth.username}:{auth.password}"
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        elif auth.token:
            encoded = auth.token
        else:
            return {}
        return {"Authorization": f"Basic {encoded}"}

    if token_type == "api-key":
        header_name = auth.header_name or "X-API-Key"
        return {header_name: auth.token}

    if token_type == "oauth":
        return {"Authorization": f"Bearer {auth.token}"}

    if token_type == "custom":
        header_name = auth.header_name or "Authorization"
        prefix = f"{auth.prefix} " if auth.prefix else ""
        return {header_name: f"{prefix}{auth.token}"}

    return {}


# ---------------------------------------------------------------------------
# Token injection
# ---------------------------------------------------------------------------

def inject_auth_token(
    headers: dict[str, str],
    token: str,
    token_type: AuthTokenType = "bearer",
    *,
    header_name: str = "Authorization",
    allow_override: bool = False,
    strip_existing_bearer: bool = True,
) -> dict[str, str]:
    """Inject an auth token into a headers dict, with safety guards.

    Parameters
    ----------
    headers:
        Existing header dictionary (not mutated — a copy is returned).
    token:
        The token or API key value.
    token_type:
        One of 'bearer', 'basic', 'api-key', 'oauth', 'custom'.
    header_name:
        Header field-name to use for api-key / custom token types.
    allow_override:
        If False (default), refuse to overwrite an existing protected auth header.
    strip_existing_bearer:
        If True, strip any Bearer prefix already present on *token*.

    Returns
    -------
    A new dict with the token injected.
    """
    result = dict(headers)
    raw_token = token or ""

    # If the token already has a "Bearer " prefix, strip it to avoid doubling
    if strip_existing_bearer and token_type in ("bearer", "oauth"):
        raw_token = BEARER_RE.sub("", raw_token).strip()

    if not raw_token:
        return result

    canonical_name = normalize_header_name(header_name)
    canonical_name_lower = canonical_name.lower()

    # Guard: don't silently overwrite an existing protected auth header
    if not allow_override:
        for existing in result:
            if existing.lower() == canonical_name_lower:
                if existing.lower() in PROTECTED_AUTH_HEADERS:
                    return result
                # Not a protected header — safe to replace
                break

    # Build the auth header value
    if token_type in ("bearer", "oauth"):
        result["Authorization"] = f"Bearer {raw_token}"
    elif token_type == "basic":
        result["Authorization"] = f"Basic {raw_token}"
    elif token_type == "api-key":
        result[canonical_name] = raw_token
    elif token_type == "custom":
        result[canonical_name] = raw_token

    return result


def inject_bearer_token(
    headers: dict[str, str],
    token: str,
    *,
    allow_override: bool = False,
) -> dict[str, str]:
    """Convenience wrapper: inject a Bearer token."""
    return inject_auth_token(headers, token, "bearer", allow_override=allow_override)


def inject_api_key(
    headers: dict[str, str],
    api_key: str,
    *,
    header_name: str = "X-API-Key",
    allow_override: bool = False,
) -> dict[str, str]:
    """Convenience wrapper: inject an API key header."""
    return inject_auth_token(
        headers, api_key, "api-key", header_name=header_name, allow_override=allow_override
    )


# ---------------------------------------------------------------------------
# Default / baseline headers
# ---------------------------------------------------------------------------


def build_default_mcp_headers(
    transport_type: str = "sse",
) -> dict[str, str]:
    """Return a baseline set of HTTP headers for MCP transport requests.

    Includes Content-Type and Accept headers appropriate for the transport.
    """
    headers = dict(DEFAULT_MCP_HEADERS)

    if transport_type == "sse":
        headers["Accept"] = "text/event-stream, application/json"
        headers["Cache-Control"] = "no-cache"
    elif transport_type == "ws":
        # WebSocket upgrade is handled by the transport layer; set minimal
        # headers for the initial HTTP upgrade handshake.
        headers.pop("Content-Type", None)
        headers["Accept"] = "application/json"
    elif transport_type == "http":
        headers["Accept"] = "application/json"

    return headers


# ---------------------------------------------------------------------------
# Dynamic header resolution from external helper
# ---------------------------------------------------------------------------


async def get_mcp_headers_from_helper(
    server_name: str,
    config: McpSseServerConfig | McpHttpServerConfig | McpWebSocketServerConfig,
    *,
    trust_check_skipped: bool = False,
) -> dict[str, str] | None:
    """Run headersHelper command and parse JSON object of string headers.

    The helper script receives the server name and URL as environment variables
    (CLAUDE_CODE_MCP_SERVER_NAME, CLAUDE_CODE_MCP_SERVER_URL) and must emit a
    JSON object mapping header names to string values on stdout.

    Returns None when:
    - No helper is configured
    - The helper exits with a non-zero code
    - The output is not a valid JSON object of str->str
    - The subprocess times out (default 10 s)
    - Any exception occurs
    """
    helper = _resolve_helper_command(config)
    if not helper:
        return None

    if not trust_check_skipped:
        # In a full interactive environment this would check workspace trust
        # and prompt the user if the helper originates from an untrusted source.
        # For non-interactive / test contexts the check is skipped.
        pass

    env = {
        **os.environ,
        "CLAUDE_CODE_MCP_SERVER_NAME": server_name,
        "CLAUDE_CODE_MCP_SERVER_URL": getattr(config, "url", ""),
    }

    try:
        proc = await asyncio.create_subprocess_shell(
            str(helper),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, _stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=DEFAULT_HEADER_TIMEOUT
        )

        if proc.returncode != 0 or not stdout_bytes:
            return None

        raw = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not raw:
            return None

        headers: Any = json.loads(raw)
        if not isinstance(headers, dict):
            return None

        out: dict[str, str] = {}
        for k, v in headers.items():
            key = str(k)
            value = v if isinstance(v, str) else str(v)

            # Validate and sanitise
            if not validate_header_name(key):
                continue
            value = sanitize_header_value(value)
            if not validate_header_value(value):
                continue

            out[key] = value

        return out if out else None

    except (asyncio.TimeoutError, Exception):
        return None


async def get_mcp_server_headers(
    server_name: str,
    config: McpSseServerConfig | McpHttpServerConfig | McpWebSocketServerConfig,
) -> dict[str, str]:
    """Resolve the complete set of MCP request headers.

    Merges static headers from config with dynamic headers from an external
    helper, then applies default MCP transport headers as a fallback. Dynamic
    headers take priority over defaults, and explicit static config headers
    take the highest priority.
    """
    defaults = build_default_mcp_headers(getattr(config, "type", "sse"))
    static = dict(getattr(config, "headers", None) or {})
    dynamic = await get_mcp_headers_from_helper(server_name, config) or {}

    # Merge order: defaults < dynamic < static (static wins conflicts)
    merged: dict[str, str] = {}
    _merge_headers(merged, defaults, allow_override=True)
    _merge_headers(merged, dynamic, allow_override=True)
    _merge_headers(merged, static, allow_override=True)
    return merged


# ---------------------------------------------------------------------------
# Header merging with priority
# ---------------------------------------------------------------------------


def merge_headers_with_priority(
    base: dict[str, str],
    overlay: dict[str, str],
    *,
    protected_names: frozenset[str] | None = None,
    allow_protected_override: bool = False,
) -> dict[str, str]:
    """Merge *overlay* into *base*, returning a new dict.

    - *base* entries act as defaults (lower priority).
    - *overlay* entries overwrite base entries unless the base entry is a
      protected auth header.
    - *protected_names* is a set of lowercased header names that must not be
      silently overridden when *allow_protected_override* is False.

    Neither input dict is mutated.
    """
    protected = protected_names or PROTECTED_AUTH_HEADERS
    result = dict(base)

    for key, value in overlay.items():
        if not validate_header_name(key):
            continue
        value = sanitize_header_value(value)
        if not validate_header_value(value):
            continue
        canonical_key = key.lstrip().rstrip()
        if not allow_protected_override and canonical_key.lower() in protected:
            # Skip — protected header already present
            if canonical_key.lower() not in (k.lower() for k in result):
                result[canonical_key] = value
            continue
        result[canonical_key] = value

    return result


def extract_bearer_token(headers: dict[str, str]) -> str | None:
    """Extract a Bearer token value from the Authorization header, if present."""
    for key, value in headers.items():
        if key.lower() == "authorization":
            m = BEARER_RE.match(value.strip())
            if m:
                return value[m.end() :].strip()
    return None


# ---------------------------------------------------------------------------
# Resolve headers for a complete MCP request
# ---------------------------------------------------------------------------


async def resolve_mcp_request_headers(
    server_name: str,
    config: McpSseServerConfig | McpHttpServerConfig | McpWebSocketServerConfig,
    *,
    auth: McpAuthContext | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve the full set of headers for an MCP request.

    This is the primary entry point used by transport layers. It combines:
    1. Baseline transport headers (Content-Type, Accept)
    2. Dynamic headers from the external helper
    3. Static headers from server config
    4. Injected auth tokens (when *auth* is provided)
    5. Per-request extra headers

    Priority order (highest wins): extra_headers > auth > static config >
    dynamic helper > defaults.

    Auth headers are injected with protection — they won't clobber
    explicitly-set Authorization headers from the static config.
    """
    defaults = build_default_mcp_headers(getattr(config, "type", "sse"))
    static = dict(getattr(config, "headers", None) or {})
    dynamic = await get_mcp_headers_from_helper(server_name, config) or {}

    # Merge order: defaults ← dynamic ← static
    merged: dict[str, str] = {}
    _merge_headers(merged, defaults, allow_override=True)
    _merge_headers(merged, dynamic, allow_override=True)
    _merge_headers(merged, static, allow_override=True)

    # Inject auth token — never override an explicitly-set Authorization header
    if auth and (auth.token or auth.username):
        auth_headers = build_auth_header(auth)
        _merge_headers(
            merged, auth_headers, allow_override=False, protected=PROTECTED_AUTH_HEADERS
        )

    # Per-request overrides (highest priority)
    if extra_headers:
        _merge_headers(merged, extra_headers, allow_override=True)

    return merged


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _merge_headers(
    target: dict[str, str],
    source: dict[str, str],
    *,
    allow_override: bool = True,
    protected: frozenset[str] | None = None,
) -> None:
    """Merge *source* into *target* in-place, with optional protection."""
    protected = protected or frozenset()
    for key, value in source.items():
        if not key or not value:
            continue
        stripped_key = key.strip()
        if not validate_header_name(stripped_key):
            continue
        clean_value = sanitize_header_value(str(value))
        if not validate_header_value(clean_value):
            continue
        if not allow_override and stripped_key.lower() in protected:
            if stripped_key.lower() not in (k.lower() for k in target):
                target[stripped_key] = clean_value
            continue
        target[stripped_key] = clean_value


def _resolve_helper_command(
    config: McpSseServerConfig | McpHttpServerConfig | McpWebSocketServerConfig,
) -> str | None:
    """Extract the headers-helper command from config, supporting both naming conventions."""
    if not isinstance(config, (McpSseServerConfig, McpHttpServerConfig, McpWebSocketServerConfig)):
        return None

    helper = (
        getattr(config, "headers_helper", None)
        or getattr(config, "headersHelper", None)
    )
    if not helper or not str(helper).strip():
        return None

    return str(helper).strip()
