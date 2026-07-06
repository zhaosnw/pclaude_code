"""
MCP OAuth client (SDK + loopback) -- full OAuth 2.0 + PKCE implementation.

Port of: src/services/mcp/auth.ts

Handles:
- OAuth metadata discovery via .well-known/oauth-authorization-server
- Auth URL construction with PKCE (S256) and state
- Token exchange (authorization_code, refresh_token grants)
- Token revocation (RFC 7009)
- Loopback redirect server for local callback handling
- IDE detection to tailor the authorization UX
- OAuthClientProvider pattern for SDK integration
- Persistent token / client-info storage via secure storage
- Cross-process refresh coordination via lockfiles
- Step-up authentication (insufficient_scope handling)
- Dynamic Client Registration (DCR) support
- URL redaction for safe logging
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from hare.services.mcp.oauth_port import build_redirect_uri, find_available_port
from hare.services.mcp.types import McpHttpServerConfig, McpSseServerConfig
from hare.utils.secure_storage.storage import get_secure_storage

logger = logging.getLogger("hare.mcp.auth")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTH_REQUEST_TIMEOUT = 30.0  # seconds per HTTP request
CALLBACK_TIMEOUT = 300       # 5 minutes for user to complete auth
MAX_LOCK_RETRIES = 5
MAX_REFRESH_ATTEMPTS = 3
TOKEN_EXPIRY_BUFFER = 30     # seconds before actual expiry to consider expired
PROACTIVE_REFRESH_WINDOW = 300  # seconds — refresh if expiring within 5 min

SENSITIVE_OAUTH_PARAMS = frozenset({
    "state", "nonce", "code_challenge", "code_verifier", "code",
})

NONSTANDARD_INVALID_GRANT_ALIASES = frozenset({
    "invalid_refresh_token",
    "expired_refresh_token",
    "token_expired",
})

CLAUDE_CONFIG_HOME = Path.home() / ".hare"


# ---------------------------------------------------------------------------
# OAuth error hierarchy
# ---------------------------------------------------------------------------

class OAuthError(Exception):
    """Base OAuth error with an RFC 6749 error code."""
    def __init__(self, message: str, error_code: str = "", status_code: int = 0) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class AuthenticationCancelledError(OAuthError):
    """User cancelled the OAuth flow (Esc, Ctrl-C, abort signal)."""
    def __init__(self) -> None:
        super().__init__("Authentication was cancelled", error_code="cancelled")


class InvalidGrantError(OAuthError):
    """Refresh token is invalid, revoked, or expired."""
    def __init__(self, message: str = "Invalid grant") -> None:
        super().__init__(message, error_code="invalid_grant")


class ServerError(OAuthError):
    """Transient server-side error (5xx)."""
    def __init__(self, message: str = "Server error", status_code: int = 500) -> None:
        super().__init__(message, error_code="server_error", status_code=status_code)


class TemporarilyUnavailableError(OAuthError):
    """Server temporarily unavailable (503)."""
    def __init__(self, message: str = "Temporarily unavailable") -> None:
        super().__init__(message, error_code="temporarily_unavailable", status_code=503)


class TooManyRequestsError(OAuthError):
    """Rate-limited (429)."""
    def __init__(self, message: str = "Too many requests") -> None:
        super().__init__(message, error_code="too_many_requests", status_code=429)


class TokenExchangeError(Exception):
    """Raised when a token exchange request fails."""

    status_code: int = 0
    error: str = ""
    error_description: str = ""

    def __str__(self) -> str:
        desc = self.error_description or self.error or "unknown error"
        return f"Token exchange failed (HTTP {self.status_code}): {desc}"


class CallbackTimeoutError(OAuthError):
    """OAuth callback did not arrive within the timeout."""
    def __init__(self, timeout: float = CALLBACK_TIMEOUT) -> None:
        super().__init__(
            f"Authentication timed out after {timeout:.0f}s",
            error_code="timeout",
        )


class CallbackStateMismatchError(OAuthError):
    """OAuth state parameter mismatch — possible CSRF."""
    def __init__(self) -> None:
        super().__init__(
            "OAuth state mismatch - possible CSRF attack",
            error_code="state_mismatch",
        )


class PortUnavailableError(OAuthError):
    """Could not bind the OAuth callback port."""
    def __init__(self, message: str = "No available ports for OAuth redirect") -> None:
        super().__init__(message, error_code="port_unavailable")


# ---------------------------------------------------------------------------
# OAuth metadata / well-known
# ---------------------------------------------------------------------------

_OAUTH_METADATA_CACHE: dict[str, dict[str, Any]] = {}


def _oauth_well_known_url(resource_url: str) -> str:
    """Build the standard OAuth metadata discovery URL."""
    base = resource_url.rstrip("/")
    return f"{base}/.well-known/oauth-authorization-server"


def _parse_oauth_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract and normalize the fields we care about from the metadata response."""
    return {
        "issuer": raw.get("issuer", ""),
        "authorization_endpoint": raw.get("authorization_endpoint", ""),
        "token_endpoint": raw.get("token_endpoint", ""),
        "registration_endpoint": raw.get("registration_endpoint"),
        "revocation_endpoint": raw.get("revocation_endpoint"),
        "scopes_supported": raw.get("scopes_supported", []),
        "response_types_supported": raw.get("response_types_supported", []),
        "code_challenge_methods_supported": raw.get(
            "code_challenge_methods_supported", ["S256"]
        ),
        "token_endpoint_auth_methods_supported": raw.get(
            "token_endpoint_auth_methods_supported", []
        ),
        "revocation_endpoint_auth_methods_supported": raw.get(
            "revocation_endpoint_auth_methods_supported"
        ),
    }


def discover_oauth_metadata(resource_url: str) -> dict[str, Any]:
    """Discover OAuth metadata from the server's .well-known endpoint.

    Results are cached in-process for the lifetime of the session.
    Falls back to RFC 8414 convention-based endpoints if discovery fails.
    """
    if not resource_url:
        return {}

    clean = resource_url.rstrip("/")
    if clean in _OAUTH_METADATA_CACHE:
        return _OAUTH_METADATA_CACHE[clean]

    well_known = _oauth_well_known_url(resource_url)

    try:
        req = urllib.request.Request(well_known)
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            raw = json.loads(body)
    except Exception:
        # Fall back to RFC 8414 convention-based endpoints
        raw = {
            "issuer": clean,
            "authorization_endpoint": f"{clean}/authorize",
            "token_endpoint": f"{clean}/token",
        }

    metadata = _parse_oauth_metadata(raw)
    _OAUTH_METADATA_CACHE[clean] = metadata
    return metadata


def get_scope_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Safely extract scope information from authorization server metadata.

    Different providers use different fields for scope information.
    """
    if not metadata:
        return None
    # Try 'scope' first (non-standard but used by some providers)
    if "scope" in metadata and isinstance(metadata["scope"], str):
        return metadata["scope"]
    # Try 'default_scope' (non-standard but used by some providers)
    if "default_scope" in metadata and isinstance(metadata["default_scope"], str):
        return metadata["default_scope"]
    # Fall back to scopes_supported (standard OAuth 2.0 field)
    scopes = metadata.get("scopes_supported", [])
    if scopes and isinstance(scopes, list):
        return " ".join(scopes)
    return None


# ---------------------------------------------------------------------------
# PKCE utilities (S256)
# ---------------------------------------------------------------------------

def _base64url_encode(data: bytes) -> str:
    """Base64url-encode bytes without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256 method).

    Returns:
        (code_verifier, code_challenge) — both base64url-encoded.
    """
    # 32 random bytes → 43-char base64url verifier (RFC 7636 §4.1)
    verifier_bytes = secrets.token_bytes(32)
    code_verifier = _base64url_encode(verifier_bytes)

    # code_challenge = BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))
    challenge_bytes = _sha256(code_verifier.encode("ascii"))
    code_challenge = _base64url_encode(challenge_bytes)

    return code_verifier, code_challenge


def generate_state() -> str:
    """Generate a cryptographically random state parameter for CSRF protection."""
    return secrets.token_hex(16)


# ---------------------------------------------------------------------------
# URL redaction for safe logging
# ---------------------------------------------------------------------------

def redact_sensitive_url_params(url: str) -> str:
    """Redact sensitive OAuth query parameters from a URL for safe logging.

    Prevents exposure of state, nonce, code_challenge, code_verifier,
    and authorization codes.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        redacted_parts: list[str] = []
        for key, values in query.items():
            if key in SENSITIVE_OAUTH_PARAMS:
                redacted_parts.append(f"{key}=[REDACTED]")
            else:
                for v in values:
                    redacted_parts.append(
                        f"{urllib.parse.quote(key, safe='')}={urllib.parse.quote(v, safe='')}"
                    )
        new_query = "&".join(redacted_parts)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


# ---------------------------------------------------------------------------
# Server key generation
# ---------------------------------------------------------------------------

def get_server_key(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
) -> str:
    """Generate a unique key for server credentials based on name + config hash.

    Prevents credentials from being reused across different servers with the
    same name or different configurations.
    """
    config_json = json.dumps(
        {
            "type": server_config.type,
            "url": server_config.url,
            "headers": server_config.headers or {},
        },
        sort_keys=True,
    )
    config_hash = hashlib.sha256(config_json.encode()).hexdigest()[:16]
    return f"{server_name}|{config_hash}"


# ---------------------------------------------------------------------------
# IDE detection for OAuth UX tailoring
# ---------------------------------------------------------------------------

_IDE_ENV_KEYS = [
    "TERM_PROGRAM",          # e.g. "vscode", "cursor"
    "VSCODE_PID",
    "VSCODE_CWD",
    "CURSOR_TRACE_ID",
    "WINDSURF_TRACE_ID",
    "JETBRAINS_REMOTE_RUN",
    "PYCHARM_HOSTED",
    "PYCHARM_MATPLOTLIB_INTERACTIVE",
    "INTELLIJ_TERMINAL_COMMAND_BLOCKING",
]

_IDE_GUESS_MAP: dict[str, str] = {
    "cursor": "cursor",
    "windsurf": "windsurf",
    "code": "vscode",
    "Visual Studio Code": "vscode",
    "pycharm": "pycharm",
    "intellij": "intellij",
    "webstorm": "webstorm",
    "phpstorm": "phpstorm",
    "rubymine": "rubymine",
    "clion": "clion",
    "goland": "goland",
    "rider": "rider",
    "datagrip": "datagrip",
    "aqua": "aqua",
    "fleet": "fleet",
    "androidstudio": "androidstudio",
}


def detect_ide_for_oauth() -> str | None:
    """Detect which IDE is hosting the current terminal session.

    Returns the IDE key (e.g. 'cursor', 'vscode', 'pycharm') or None.
    Used to decide whether to open the browser automatically or display
    a URL for the user to copy-paste.
    """
    # 1. Env-var heuristics
    term_program = os.environ.get("TERM_PROGRAM", "")
    term_lower = term_program.lower()

    for key, ide in _IDE_GUESS_MAP.items():
        if key in term_lower:
            return ide

    # JetBrains detection via env vars
    for var in [
        "PYCHARM_HOSTED",
        "PYCHARM_MATPLOTLIB_INTERACTIVE",
        "INTELLIJ_TERMINAL_COMMAND_BLOCKING",
    ]:
        if os.environ.get(var):
            if "PYCHARM" in var:
                return "pycharm"
            if "INTELLIJ" in var:
                return "intellij"
            return "jetbrains"

    # VS Code / Cursor detection
    if os.environ.get("VSCODE_PID") or os.environ.get("VSCODE_CWD"):
        if os.environ.get("CURSOR_TRACE_ID"):
            return "cursor"
        return "vscode"

    # Windsurf
    if os.environ.get("WINDSURF_TRACE_ID"):
        return "windsurf"

    # 2. Check common IDE-specific env vars
    if os.environ.get("JETBRAINS_REMOTE_RUN"):
        return "jetbrains"

    # 3. Terminal-based detection (e.g., warp, iterm2 don't mean an IDE)
    return None


def is_ide_terminal() -> bool:
    """Check whether the terminal is embedded inside an IDE."""
    return detect_ide_for_oauth() is not None


# ---------------------------------------------------------------------------
# Browser opening
# ---------------------------------------------------------------------------

def _open_browser(url: str) -> bool:
    """Attempt to open *url* in the system browser. Returns True on success."""
    import shutil
    import subprocess

    for cmd in (["open"], ["xdg-open"], ["start"]):
        binary = shutil.which(cmd[0])
        if binary:
            try:
                subprocess.Popen(
                    [binary, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
# OAuth authorization URL construction
# ---------------------------------------------------------------------------

def build_oauth_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str] | None = None,
    code_challenge: str | None = None,
    state: str | None = None,
    audience: str | None = None,
) -> str:
    """Build a complete OAuth 2.0 authorization URL with PKCE parameters.

    Args:
        authorization_endpoint: The /authorize endpoint URL.
        client_id: OAuth client ID.
        redirect_uri: Loopback or custom redirect URI.
        scopes: List of OAuth scopes to request.
        code_challenge: PKCE S256 code_challenge (recommended).
        state: Opaque state value for CSRF protection.
        audience: Optional ``audience`` param (Auth0-style).

    Returns:
        Fully constructed authorization URL string.
    """
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }

    if scopes:
        params["scope"] = " ".join(scopes)

    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"

    if state:
        params["state"] = state

    if audience:
        params["audience"] = audience

    # Separate fragment from the base endpoint URL (just in case)
    base_url, fragment = urllib.parse.urldefrag(authorization_endpoint)
    query_string = urllib.parse.urlencode(params)

    if "?" in base_url:
        return f"{base_url}&{query_string}"
    return f"{base_url}?{query_string}"


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

async def _post_form(url: str, data: dict[str, str], *, timeout: float = 15.0) -> Any:
    """POST form-urlencoded data and parse the JSON response."""
    encoded = urllib.parse.urlencode(data).encode("ascii")

    def _do_post() -> Any:
        req = urllib.request.Request(url, data=encoded, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            try:
                error_data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                error_data = {}
            raise TokenExchangeError(
                status_code=e.code,
                error=error_data.get("error", str(e)),
                error_description=error_data.get("error_description", body[:500]),
            ) from e
        except urllib.error.URLError as e:
            raise TokenExchangeError(
                status_code=0,
                error="network_error",
                error_description=str(e.reason),
            ) from e

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_post)


async def exchange_code_for_tokens(
    *,
    token_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Exchange an authorization code for access / refresh tokens.

    Implements the OAuth 2.0 authorization_code grant with PKCE.

    Returns the raw token endpoint response dict (access_token, refresh_token,
    expires_in, token_type, scope, etc.).
    """
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    return await _post_form(token_endpoint, data, timeout=timeout)


async def exchange_refresh_token(
    *,
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
    scopes: list[str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Exchange a refresh token for a new access token set.

    Implements the OAuth 2.0 refresh_token grant.
    """
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if scopes:
        data["scope"] = " ".join(scopes)

    return await _post_form(token_endpoint, data, timeout=timeout)


# ---------------------------------------------------------------------------
# Token revocation (RFC 7009)
# ---------------------------------------------------------------------------

async def _revoke_single_token(
    *,
    server_name: str,
    endpoint: str,
    token: str,
    token_type_hint: str = "refresh_token",
    client_id: str | None = None,
    client_secret: str | None = None,
    access_token: str | None = None,
    auth_method: str = "client_secret_basic",
    timeout: float = 15.0,
) -> None:
    """Revoke a single token on the OAuth server (RFC 7009).

    For public clients, authenticates via client_id in request body.
    For confidential clients, uses either Basic auth (header) or
    client_secret_post (body), per the auth_method parameter.

    Falls back to Bearer auth if the server returns 401 to the primary
    method (defensive compatibility with non-RFC-7009-compliant servers).
    """
    import urllib.request as _req

    params: dict[str, str] = {
        "token": token,
        "token_type_hint": token_type_hint,
    }
    headers: dict[str, str] = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    # Authenticate per RFC 6749 §2.3
    if client_id and client_secret:
        if auth_method == "client_secret_post":
            params["client_id"] = client_id
            params["client_secret"] = client_secret
        else:
            # client_secret_basic
            credentials = f"{urllib.parse.quote(client_id, safe='')}:{urllib.parse.quote(client_secret, safe='')}"
            encoded_creds = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded_creds}"
    elif client_id:
        params["client_id"] = client_id
    else:
        logger.debug(
            "No client_id available for %s revocation — server may reject",
            token_type_hint,
        )

    encoded = urllib.parse.urlencode(params).encode("ascii")

    def _do_revoke(headers_override: dict[str, str] | None = None) -> None:
        hdrs = dict(headers)
        if headers_override:
            hdrs.update(headers_override)
        req = _req.Request(endpoint, data=encoded, headers=hdrs, method="POST")
        with _req.urlopen(req, timeout=timeout) as resp:
            pass  # 200 OK is success; body may be empty

    try:
        await asyncio.get_event_loop().run_in_executor(None, _do_revoke)
        logger.debug("Successfully revoked %s for %s", token_type_hint, server_name)
    except urllib.error.HTTPError as e:
        # Fallback: retry with Bearer auth if server returned 401
        if e.code == 401 and access_token:
            logger.debug(
                "Got 401 revoking %s for %s, retrying with Bearer auth",
                token_type_hint, server_name,
            )
            bearer_headers = {**headers, "Authorization": f"Bearer {access_token}"}
            # Remove client_secret_post params for the retry
            params.pop("client_id", None)
            params.pop("client_secret", None)
            encoded2 = urllib.parse.urlencode(params).encode("ascii")

            def _do_revoke_bearer() -> None:
                req2 = _req.Request(endpoint, data=encoded2, headers=bearer_headers, method="POST")
                with _req.urlopen(req2, timeout=timeout) as resp2:
                    pass

            try:
                await asyncio.get_event_loop().run_in_executor(None, _do_revoke_bearer)
                logger.debug(
                    "Successfully revoked %s with Bearer auth for %s",
                    token_type_hint, server_name,
                )
            except Exception as exc:
                logger.debug(
                    "Bearer-auth revocation also failed for %s (%s): %s",
                    server_name, token_type_hint, exc,
                )
        else:
            logger.debug(
                "Failed to revoke %s (%s): HTTP %s",
                token_type_hint, server_name, e.code,
            )
    except Exception as exc:
        logger.debug(
            "Failed to revoke %s (%s): %s",
            token_type_hint, server_name, exc,
        )


async def revoke_server_tokens(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
    *,
    preserve_step_up_state: bool = False,
) -> None:
    """Revoke tokens on the OAuth server if a revocation endpoint is available.

    Per RFC 7009, revokes the refresh token first (the long-lived credential),
    then the access token. Revoking the refresh token prevents generation of
    new access tokens and many servers implicitly invalidate associated
    access tokens.

    Revocation is best-effort: errors are logged but never raised.
    """
    storage = get_secure_storage()
    server_key = get_server_key(server_name, server_config)
    token_data = _load_mcp_oauth_entry(server_key)

    if not token_data or (not token_data.get("accessToken") and not token_data.get("refreshToken")):
        logger.debug("No tokens to revoke for %s", server_name)
        return

    # Attempt server-side revocation
    if token_data.get("accessToken") or token_data.get("refreshToken"):
        try:
            # Use the persisted AS URL for discovery, or fall back to the MCP URL
            as_url = (
                _get_discovery_as_url(token_data)
                or server_config.url
            )
            metadata = discover_oauth_metadata(as_url)
            revocation_endpoint = metadata.get("revocation_endpoint")

            if not revocation_endpoint:
                logger.debug("Server %s does not support token revocation", server_name)
            else:
                # Determine auth method for revocation
                auth_methods = (
                    metadata.get("revocation_endpoint_auth_methods_supported")
                    or metadata.get("token_endpoint_auth_methods_supported")
                    or []
                )
                auth_method: str = "client_secret_basic"
                if ("client_secret_post" in auth_methods
                        and "client_secret_basic" not in auth_methods):
                    auth_method = "client_secret_post"

                logger.debug(
                    "Revoking tokens for %s via %s (%s)",
                    server_name, revocation_endpoint, auth_method,
                )

                rev_endpoint = str(revocation_endpoint)
                client_id = token_data.get("clientId")
                client_secret = token_data.get("clientSecret")
                access_token = token_data.get("accessToken")

                # Revoke refresh token first (more important)
                refresh_token = token_data.get("refreshToken")
                if refresh_token:
                    try:
                        await _revoke_single_token(
                            server_name=server_name,
                            endpoint=rev_endpoint,
                            token=refresh_token,
                            token_type_hint="refresh_token",
                            client_id=client_id,
                            client_secret=client_secret,
                            access_token=access_token,
                            auth_method=auth_method,
                        )
                    except Exception as exc:
                        logger.debug(
                            "Failed to revoke refresh token for %s: %s",
                            server_name, exc,
                        )

                # Then revoke access token
                if access_token:
                    try:
                        await _revoke_single_token(
                            server_name=server_name,
                            endpoint=rev_endpoint,
                            token=access_token,
                            token_type_hint="access_token",
                            client_id=client_id,
                            client_secret=client_secret,
                            access_token=access_token,
                            auth_method=auth_method,
                        )
                    except Exception as exc:
                        logger.debug(
                            "Failed to revoke access token for %s: %s",
                            server_name, exc,
                        )
        except Exception as exc:
            logger.debug("Failed to revoke tokens for %s: %s", server_name, exc)

    # Always clear local tokens
    clear_server_tokens_from_storage(server_name, server_config)

    # Optionally preserve step-up auth state
    if preserve_step_up_state and token_data:
        step_up_scope = token_data.get("stepUpScope")
        discovery_state = token_data.get("discoveryState")
        if step_up_scope or discovery_state:
            fresh = _load_all_mcp_oauth() or {}
            entry = fresh.get(server_key, {})
            entry.update({
                "serverName": server_name,
                "serverUrl": server_config.url,
                "accessToken": entry.get("accessToken", ""),
                "expiresAt": entry.get("expiresAt", 0),
            })
            if step_up_scope:
                entry["stepUpScope"] = step_up_scope
            if discovery_state:
                entry["discoveryState"] = {
                    "authorizationServerUrl": discovery_state.get("authorizationServerUrl"),
                    "resourceMetadataUrl": discovery_state.get("resourceMetadataUrl"),
                }
            fresh[server_key] = entry
            _save_all_mcp_oauth(fresh)
            logger.debug("Preserved step-up auth state for %s", server_name)


# ---------------------------------------------------------------------------
# Secure storage helpers for MCP OAuth tokens
# ---------------------------------------------------------------------------

_STORAGE_KEY = "mcpOAuth"
_CLIENT_CONFIG_KEY = "mcpOAuthClientConfig"


def _load_all_mcp_oauth() -> dict[str, Any]:
    """Load the full mcpOAuth dict from secure storage."""
    try:
        raw = get_secure_storage().get(_STORAGE_KEY)
        if raw:
            return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _save_all_mcp_oauth(data: dict[str, Any]) -> None:
    """Persist the full mcpOAuth dict to secure storage."""
    get_secure_storage().set(_STORAGE_KEY, json.dumps(data))


def _load_mcp_oauth_entry(server_key: str) -> dict[str, Any] | None:
    """Load a single server's OAuth entry."""
    all_data = _load_all_mcp_oauth()
    return all_data.get(server_key)


def _save_mcp_oauth_entry(server_key: str, entry: dict[str, Any]) -> None:
    """Save a single server's OAuth entry."""
    all_data = _load_all_mcp_oauth()
    existing = all_data.get(server_key, {})
    existing.update(entry)
    all_data[server_key] = existing
    _save_all_mcp_oauth(all_data)


def _get_discovery_as_url(token_data: dict[str, Any]) -> str | None:
    """Extract the authorization server URL from persisted discovery state."""
    ds = token_data.get("discoveryState")
    if isinstance(ds, dict):
        return ds.get("authorizationServerUrl")
    return None


def clear_server_tokens_from_storage(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
) -> None:
    """Remove stored OAuth tokens for a server from secure storage."""
    server_key = get_server_key(server_name, server_config)
    all_data = _load_all_mcp_oauth()
    if server_key in all_data:
        del all_data[server_key]
        _save_all_mcp_oauth(all_data)
        logger.debug("Cleared stored tokens for %s", server_name)


def has_mcp_discovery_but_no_token(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
) -> bool:
    """Check if we have probed this server before but hold no credentials."""
    server_key = get_server_key(server_name, server_config)
    entry = _load_mcp_oauth_entry(server_key)
    if entry is None:
        return False
    return not entry.get("accessToken") and not entry.get("refreshToken")


# ---------------------------------------------------------------------------
# Client metadata helpers
# ---------------------------------------------------------------------------

def _build_default_scopes() -> list[str]:
    """Default OAuth scopes for MCP server access."""
    return ["openid", "profile", "email", "mcp:read", "mcp:write"]


def _extract_redirect_uris(port: int) -> list[str]:
    """Build a list of permitted redirect URIs for the local loopback."""
    return [
        build_redirect_uri(port),
        f"http://localhost:{port}/callback",
        f"http://127.0.0.1:{port}/",
        f"http://localhost:{port}/",
    ]


# ---------------------------------------------------------------------------
# Client secret management
# ---------------------------------------------------------------------------

def _load_client_configs() -> dict[str, Any]:
    try:
        raw = get_secure_storage().get(_CLIENT_CONFIG_KEY)
        if raw:
            return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _save_client_configs(data: dict[str, Any]) -> None:
    get_secure_storage().set(_CLIENT_CONFIG_KEY, json.dumps(data))


def save_mcp_client_secret(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
    client_secret: str,
) -> None:
    """Store a client secret for an MCP server in secure storage."""
    server_key = get_server_key(server_name, server_config)
    configs = _load_client_configs()
    configs[server_key] = {"clientSecret": client_secret}
    _save_client_configs(configs)


def get_mcp_client_config(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
) -> dict[str, Any] | None:
    """Retrieve stored client config for an MCP server."""
    server_key = get_server_key(server_name, server_config)
    configs = _load_client_configs()
    return configs.get(server_key)


def clear_mcp_client_config(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
) -> None:
    """Remove stored client config for an MCP server."""
    server_key = get_server_key(server_name, server_config)
    configs = _load_client_configs()
    if server_key in configs:
        del configs[server_key]
        _save_client_configs(configs)


async def read_client_secret() -> str:
    """Securely prompt the user for an OAuth client secret.

    Reads from MCP_CLIENT_SECRET env var if set; otherwise prompts on stderr
    with echo disabled.
    """
    env_secret = os.environ.get("MCP_CLIENT_SECRET")
    if env_secret:
        return env_secret

    if not sys.stdin.isatty():
        raise RuntimeError(
            "No TTY available to prompt for client secret. "
            "Set MCP_CLIENT_SECRET env var instead."
        )

    import termios
    import tty

    loop = asyncio.get_event_loop()

    def _prompt() -> str:
        sys.stderr.write("Enter OAuth client secret: ")
        sys.stderr.flush()
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            secret_parts: list[str] = []
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\n", "\r"):
                    break
                if ch == "\x03":  # Ctrl-C
                    sys.stderr.write("\n")
                    raise KeyboardInterrupt("Cancelled")
                if ch in ("\x7f", "\x08"):  # Backspace
                    if secret_parts:
                        secret_parts.pop()
                else:
                    secret_parts.append(ch)
            return "".join(secret_parts)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            sys.stderr.write("\n")

    return await loop.run_in_executor(None, _prompt)


# ---------------------------------------------------------------------------
# McpOAuthTokens — token state holder
# ---------------------------------------------------------------------------

@dataclass
class McpOAuthTokens:
    """Holds OAuth token state for an MCP server connection."""

    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None
    token_type: str = "Bearer"
    scope: str = ""

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at - TOKEN_EXPIRY_BUFFER

    @property
    def expires_in(self) -> float:
        """Seconds until token expiry (may be negative if expired)."""
        if self.expires_at is None:
            return float("inf")
        return self.expires_at - time.time()

    @property
    def should_refresh_proactively(self) -> bool:
        """True if the token will expire soon and we have a refresh token."""
        return (
            self.refresh_token is not None
            and self.expires_at is not None
            and self.expires_in <= PROACTIVE_REFRESH_WINDOW
        )

    def update_from_response(self, response: dict[str, Any]) -> None:
        """Update tokens from a token endpoint response."""
        self.access_token = response.get("access_token", self.access_token)
        new_refresh = response.get("refresh_token")
        if new_refresh:
            self.refresh_token = new_refresh
        expires_in = response.get("expires_in")
        if isinstance(expires_in, (int, float)):
            if expires_in > 0:
                self.expires_at = time.time() + float(expires_in)
            elif expires_in == 0:
                self.expires_at = time.time()  # token already expired
        self.token_type = response.get("token_type", self.token_type)
        self.scope = response.get("scope", self.scope)


@dataclass
class McpOAuthClientMetadata:
    """Client metadata registered or sent during OAuth flows."""

    client_name: str = "hare-code"
    redirect_uris: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# OAuthClientProvider — ClaudeAuthProvider port
# ---------------------------------------------------------------------------

class OAuthClientProvider:
    """OAuth client provider for MCP SDK integration.

    Port of the TS ``ClaudeAuthProvider`` implementing the
    ``OAuthClientProvider`` interface.

    Manages:
    - Client metadata for Dynamic Client Registration (DCR)
    - In-memory PKCE verifier + authorization URL
    - Token storage/retrieval via secure storage
    - Discovery state persistence (AS URL, resource metadata URL)
    - Coordinated token refresh with cross-process lockfiles
    - Step-up authentication (insufficient_scope handling)
    """

    def __init__(
        self,
        server_name: str,
        server_config: McpSseServerConfig | McpHttpServerConfig,
        redirect_uri: str = "",
        handle_redirection: bool = False,
        on_authorization_url: Callable[[str], None] | None = None,
        skip_browser_open: bool = False,
    ) -> None:
        self.server_name = server_name
        self.server_config = server_config
        self.redirect_uri = redirect_uri or build_redirect_uri()
        self.handle_redirection = handle_redirection
        self.on_authorization_url = on_authorization_url
        self.skip_browser_open = skip_browser_open

        # In-memory state
        self._code_verifier: str | None = None
        self._authorization_url: str | None = None
        self._state: str | None = None
        self._scopes: str | None = None
        self._metadata: dict[str, Any] | None = None
        self._refresh_in_progress: asyncio.Task | None = None
        self._pending_step_up_scope: str | None = None

    # -- Properties -----------------------------------------------------------

    @property
    def redirect_url(self) -> str:
        return self.redirect_uri

    @property
    def authorization_url(self) -> str | None:
        return self._authorization_url

    @property
    def server_key(self) -> str:
        return get_server_key(self.server_name, self.server_config)

    # -- Client metadata (DCR) ------------------------------------------------

    @property
    def client_metadata(self) -> dict[str, Any]:
        """Build client metadata for Dynamic Client Registration."""
        metadata: dict[str, Any] = {
            "client_name": f"Claude Code ({self.server_name})",
            "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",  # Public client
        }
        # Include scope from metadata if available
        metadata_scope = get_scope_from_metadata(self._metadata)
        if metadata_scope:
            metadata["scope"] = metadata_scope
            logger.debug(
                "Using scope from metadata for %s: %s",
                self.server_name, metadata_scope,
            )
        return metadata

    @property
    def client_metadata_url(self) -> str | None:
        """CIMD (SEP-991): URL-based client_id.

        When the auth server advertises client_id_metadata_document_supported,
        the SDK uses this URL as the client_id instead of performing DCR.
        Override via MCP_OAUTH_CLIENT_METADATA_URL env var.
        """
        override = os.environ.get("MCP_OAUTH_CLIENT_METADATA_URL")
        if override:
            logger.debug("Using CIMD URL from env for %s: %s", self.server_name, override)
            return override
        return os.environ.get(
            "MCP_CLIENT_METADATA_URL",
            "https://console.anthropic.com/mcp/.well-known/client-metadata",
        )

    # -- Metadata -------------------------------------------------------------

    def set_metadata(self, metadata: dict[str, Any]) -> None:
        """Cache discovered authorization server metadata."""
        self._metadata = metadata

    # -- State ----------------------------------------------------------------

    async def state(self) -> str:
        """Generate or return the OAuth state parameter."""
        if not self._state:
            self._state = generate_state()
            logger.debug("Generated new OAuth state for %s", self.server_name)
        return self._state

    # -- Client information (DCR result) --------------------------------------

    async def client_information(self) -> dict[str, Any] | None:
        """Retrieve stored client information (DCR result or pre-configured)."""
        server_key = self.server_key
        stored = _load_mcp_oauth_entry(server_key)

        # Check session credentials first (from DCR or previous auth)
        if stored and stored.get("clientId"):
            logger.debug("Found client info for %s", self.server_name)
            result: dict[str, Any] = {"client_id": stored["clientId"]}
            if stored.get("clientSecret"):
                result["client_secret"] = stored["clientSecret"]
            return result

        # Fallback: pre-configured client ID from server config headers
        headers = getattr(self.server_config, "headers", {}) or {}
        config_client_id = None
        for k, v in headers.items():
            if k.lower() == "x-oauth-client-id":
                config_client_id = v
                break

        if config_client_id:
            client_config = get_mcp_client_config(self.server_name, self.server_config)
            logger.debug("Using pre-configured client ID for %s", self.server_name)
            result = {"client_id": config_client_id}
            if client_config and client_config.get("clientSecret"):
                result["client_secret"] = client_config["clientSecret"]
            return result

        logger.debug("No client info found for %s", self.server_name)
        return None

    async def save_client_information(
        self, client_information: dict[str, Any]
    ) -> None:
        """Persist DCR result (client_id + client_secret)."""
        server_key = self.server_key
        existing = _load_mcp_oauth_entry(server_key) or {}
        existing.update({
            "serverName": self.server_name,
            "serverUrl": self.server_config.url,
            "clientId": client_information.get("client_id"),
            "clientSecret": client_information.get("client_secret"),
            # Preserve existing token data with defaults for required fields
            "accessToken": existing.get("accessToken", ""),
            "expiresAt": existing.get("expiresAt", 0),
        })
        _save_mcp_oauth_entry(server_key, existing)

    # -- Tokens ---------------------------------------------------------------

    async def tokens(self) -> dict[str, Any] | None:
        """Retrieve stored tokens, refreshing proactively if needed.

        Returns a dict with access_token, refresh_token, expires_in, scope,
        token_type — compatible with the MCP SDK's OAuthTokens schema.
        Cross-process token changes are picked up via re-reading storage.
        """
        server_key = self.server_key
        token_data = _load_mcp_oauth_entry(server_key)

        if not token_data:
            logger.debug("No token data found for %s", self.server_name)
            return None

        expires_in = (token_data.get("expiresAt", 0) - time.time())

        # Step-up check: if a 403 insufficient_scope was detected and the
        # current token doesn't have the requested scope, omit refresh_token
        # so the SDK skips refresh and falls through to PKCE flow.
        current_scopes = (token_data.get("scope", "")).split()
        needs_step_up = (
            self._pending_step_up_scope is not None
            and any(
                s not in current_scopes
                for s in self._pending_step_up_scope.split()
            )
        )
        if needs_step_up:
            logger.debug(
                "Step-up pending for %s (%s), omitting refresh_token",
                self.server_name, self._pending_step_up_scope,
            )

        # If token is expired and no refresh token, return None
        if expires_in <= 0 and not token_data.get("refreshToken"):
            logger.debug("Token expired without refresh token for %s", self.server_name)
            return None

        # Proactive refresh if expiring soon and we have a refresh token
        if expires_in <= PROACTIVE_REFRESH_WINDOW and token_data.get("refreshToken") and not needs_step_up:
            if not self._refresh_in_progress:
                logger.debug(
                    "Token expires in %.0fs for %s, attempting proactive refresh",
                    expires_in, self.server_name,
                )
                refresh_token = token_data["refreshToken"]
                self._refresh_in_progress = asyncio.ensure_future(
                    self.refresh_authorization(refresh_token)
                )

                def _clear_refresh(_task: asyncio.Task) -> None:
                    self._refresh_in_progress = None

                self._refresh_in_progress.add_done_callback(_clear_refresh)
            else:
                logger.debug(
                    "Token refresh already in progress for %s, reusing",
                    self.server_name,
                )

            try:
                refreshed = await self._refresh_in_progress
                if refreshed:
                    logger.debug("Token refreshed successfully for %s", self.server_name)
                    return refreshed
                logger.debug(
                    "Token refresh failed for %s, returning current tokens",
                    self.server_name,
                )
            except Exception as exc:
                logger.debug(
                    "Token refresh error for %s: %s", self.server_name, exc,
                )

        # Return current tokens
        tokens_dict = {
            "access_token": token_data.get("accessToken", ""),
            "refresh_token": None if needs_step_up else token_data.get("refreshToken"),
            "expires_in": int(max(0, expires_in)),
            "scope": token_data.get("scope", ""),
            "token_type": "Bearer",
        }
        logger.debug("Returning tokens for %s (expires_in=%ds)", self.server_name, tokens_dict["expires_in"])
        return tokens_dict

    async def save_tokens(self, tokens: dict[str, Any]) -> None:
        """Persist OAuth tokens after a successful auth or refresh."""
        self._pending_step_up_scope = None
        server_key = self.server_key
        existing = _load_mcp_oauth_entry(server_key) or {}

        expires_in = tokens.get("expires_in", 3600)
        expires_at = time.time() + (float(expires_in) if isinstance(expires_in, (int, float)) else 3600)

        logger.debug("Saving tokens for %s (expires_in=%s)", self.server_name, expires_in)
        existing.update({
            "serverName": self.server_name,
            "serverUrl": self.server_config.url,
            "accessToken": tokens.get("access_token", ""),
            "refreshToken": tokens.get("refresh_token"),
            "expiresAt": expires_at,
            "scope": tokens.get("scope", ""),
        })
        _save_mcp_oauth_entry(server_key, existing)

    # -- Authorization redirect ------------------------------------------------

    async def redirect_to_authorization(self, authorization_url: str) -> None:
        """Handle the authorization redirect.

        Called by the SDK to redirect the user to the authorization URL.
        Stores the URL, extracts scopes, and optionally opens the browser.
        """
        self._authorization_url = authorization_url

        # Extract scopes from the URL
        try:
            parsed = urllib.parse.urlparse(authorization_url)
            query = urllib.parse.parse_qs(parsed.query)
            scopes = query.get("scope", [None])[0]
        except Exception:
            scopes = None

        logger.debug(
            "Authorization URL for %s: %s",
            self.server_name, redact_sensitive_url_params(authorization_url),
        )

        if scopes:
            self._scopes = scopes
            logger.debug("Captured scopes for %s: %s", self.server_name, scopes)
        else:
            metadata_scope = get_scope_from_metadata(self._metadata)
            if metadata_scope:
                self._scopes = metadata_scope
                logger.debug(
                    "Using scopes from metadata for %s: %s",
                    self.server_name, metadata_scope,
                )
            else:
                logger.debug("No scopes available for %s", self.server_name)

        # Persist scope for step-up auth
        if self._scopes and not self.handle_redirection:
            server_key = self.server_key
            existing = _load_mcp_oauth_entry(server_key)
            if existing:
                existing["stepUpScope"] = self._scopes
                _save_mcp_oauth_entry(server_key, existing)
                logger.debug("Persisted step-up scope for %s: %s", self.server_name, self._scopes)

        if not self.handle_redirection:
            logger.debug("Redirection handling disabled for %s, skipping", self.server_name)
            return

        # Validate URL scheme
        if not authorization_url.startswith(("http://", "https://")):
            raise ValueError(
                "Invalid authorization URL: must use http:// or https:// scheme"
            )

        # Notify caller before opening browser
        if self.on_authorization_url:
            self.on_authorization_url(authorization_url)

        if not self.skip_browser_open:
            logger.debug(
                "Opening authorization URL for %s: %s",
                self.server_name, redact_sensitive_url_params(authorization_url),
            )
            success = _open_browser(authorization_url)
            if not success:
                logger.debug(
                    "Browser didn't open automatically for %s. URL shown in UI.",
                    self.server_name,
                )
        else:
            logger.debug("Skipping browser open for %s", self.server_name)

    # -- PKCE code verifier ---------------------------------------------------

    async def save_code_verifier(self, code_verifier: str) -> None:
        """Store the PKCE code verifier in memory."""
        logger.debug("Saving code verifier for %s", self.server_name)
        self._code_verifier = code_verifier

    async def code_verifier(self) -> str:
        """Retrieve the stored PKCE code verifier."""
        if not self._code_verifier:
            logger.debug("No code verifier saved for %s", self.server_name)
            raise ValueError("No code verifier saved")
        logger.debug("Returning code verifier for %s", self.server_name)
        return self._code_verifier

    # -- Credential invalidation -----------------------------------------------

    async def invalidate_credentials(
        self, scope: str = "all"
    ) -> None:
        """Invalidate stored credentials by scope.

        Scope values:
        - "all": Clear all stored data for this server
        - "client": Clear only client_id/client_secret
        - "tokens": Clear only access/refresh tokens
        - "verifier": Clear only the in-memory code verifier
        - "discovery": Clear discovery state + step-up scope
        """
        server_key = self.server_key
        token_data = _load_mcp_oauth_entry(server_key)

        if scope == "verifier":
            self._code_verifier = None
            return

        if not token_data:
            return

        if scope == "all":
            all_data = _load_all_mcp_oauth()
            if server_key in all_data:
                del all_data[server_key]
                _save_all_mcp_oauth(all_data)
        elif scope == "client":
            token_data.pop("clientId", None)
            token_data.pop("clientSecret", None)
            _save_mcp_oauth_entry(server_key, token_data)
        elif scope == "tokens":
            token_data["accessToken"] = ""
            token_data.pop("refreshToken", None)
            token_data["expiresAt"] = 0
            _save_mcp_oauth_entry(server_key, token_data)
        elif scope == "discovery":
            token_data.pop("discoveryState", None)
            token_data.pop("stepUpScope", None)
            _save_mcp_oauth_entry(server_key, token_data)

        logger.debug(
            "Invalidated credentials for %s (scope=%s)", self.server_name, scope,
        )

    # -- Discovery state -------------------------------------------------------

    async def save_discovery_state(
        self, state: dict[str, Any]
    ) -> None:
        """Persist OAuth discovery URLs (NOT full metadata blobs).

        Only stores authorizationServerUrl and resourceMetadataUrl to avoid
        keychain/credential-store size limits.
        """
        server_key = self.server_key
        logger.debug(
            "Saving discovery state for %s (authServer=%s)",
            self.server_name, state.get("authorizationServerUrl"),
        )
        existing = _load_mcp_oauth_entry(server_key) or {}
        existing.update({
            "serverName": self.server_name,
            "serverUrl": self.server_config.url,
            "accessToken": existing.get("accessToken", ""),
            "expiresAt": existing.get("expiresAt", 0),
            "discoveryState": {
                "authorizationServerUrl": state.get("authorizationServerUrl"),
                "resourceMetadataUrl": state.get("resourceMetadataUrl"),
            },
        })
        _save_mcp_oauth_entry(server_key, existing)

    async def discovery_state(self) -> dict[str, Any] | None:
        """Retrieve persisted discovery state, or re-discover if needed."""
        server_key = self.server_key
        token_data = _load_mcp_oauth_entry(server_key)
        cached = token_data.get("discoveryState") if token_data else None

        if cached and cached.get("authorizationServerUrl"):
            logger.debug(
                "Returning cached discovery state for %s (authServer=%s)",
                self.server_name, cached["authorizationServerUrl"],
            )
            return cached

        # Check config hint for direct metadata URL
        config = self.server_config
        headers = getattr(config, "headers", {}) or {}
        metadata_url = (
            headers.get("x-oauth-auth-server-metadata-url")
            or headers.get("X-OAuth-Auth-Server-Metadata-Url")
        )
        if metadata_url:
            logger.debug(
                "Fetching metadata from configured URL for %s: %s",
                self.server_name, metadata_url,
            )
            try:
                metadata = discover_oauth_metadata(metadata_url)
                if metadata and metadata.get("issuer"):
                    return {
                        "authorizationServerUrl": metadata["issuer"],
                    }
            except Exception as exc:
                logger.debug(
                    "Failed to fetch from configured metadata URL for %s: %s",
                    self.server_name, exc,
                )

        return None

    # -- Token refresh ---------------------------------------------------------

    async def refresh_authorization(
        self, refresh_token: str
    ) -> dict[str, Any] | None:
        """Refresh an access token using a refresh token.

        Coordinates across processes using a lockfile to prevent
        concurrent refreshes.

        Retries up to MAX_REFRESH_ATTEMPTS on transient errors with
        exponential backoff.
        """
        from hare.utils.lockfile import lock as acquire_lock

        server_key = self.server_key
        Claude_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
        sanitized_key = re.sub(r"[^a-zA-Z0-9]", "_", server_key)
        lockfile_path = str(Claude_CONFIG_HOME / f"mcp-refresh-{sanitized_key}.lock")

        # Acquire cross-process lock
        release_fn = None
        for retry in range(MAX_LOCK_RETRIES):
            try:
                logger.debug(
                    "Acquiring refresh lock for %s (attempt %d)",
                    self.server_name, retry + 1,
                )
                release_fn = await acquire_lock(lockfile_path)
                logger.debug("Acquired refresh lock for %s", self.server_name)
                break
            except Exception:
                wait_ms = 1000 + (secrets.randbits(10) % 1000)
                logger.debug(
                    "Refresh lock held by another process for %s, waiting %dms (attempt %d/%d)",
                    self.server_name, wait_ms, retry + 1, MAX_LOCK_RETRIES,
                )
                await asyncio.sleep(wait_ms / 1000.0)

        try:
            # Re-read tokens after acquiring lock — another process may have refreshed
            token_data = _load_mcp_oauth_entry(server_key)
            if token_data:
                expires_in = token_data.get("expiresAt", 0) - time.time()
                if expires_in > PROACTIVE_REFRESH_WINDOW:
                    logger.debug(
                        "Another process already refreshed tokens for %s (expires in %.0fs)",
                        self.server_name, expires_in,
                    )
                    return {
                        "access_token": token_data.get("accessToken", ""),
                        "refresh_token": token_data.get("refreshToken"),
                        "expires_in": int(max(0, expires_in)),
                        "scope": token_data.get("scope", ""),
                        "token_type": "Bearer",
                    }
                # Use the freshest refresh token from storage
                if token_data.get("refreshToken"):
                    refresh_token = token_data["refreshToken"]

            return await self._do_refresh(refresh_token)
        finally:
            if release_fn:
                try:
                    await release_fn()
                    logger.debug("Released refresh lock for %s", self.server_name)
                except Exception:
                    logger.debug("Failed to release refresh lock for %s", self.server_name)

    async def _do_refresh(
        self, refresh_token: str
    ) -> dict[str, Any] | None:
        """Perform the actual token refresh with retries on transient errors."""
        for attempt in range(1, MAX_REFRESH_ATTEMPTS + 1):
            try:
                logger.debug(
                    "Starting token refresh for %s (attempt %d/%d)",
                    self.server_name, attempt, MAX_REFRESH_ATTEMPTS,
                )

                # Discover metadata
                metadata = self._metadata
                if not metadata:
                    cached = await self.discovery_state()
                    if cached and cached.get("authorizationServerUrl"):
                        as_url = cached["authorizationServerUrl"]
                        logger.debug(
                            "Re-discovering metadata from persisted AS URL for %s: %s",
                            self.server_name, as_url,
                        )
                        metadata = discover_oauth_metadata(as_url)
                if not metadata:
                    metadata = discover_oauth_metadata(self.server_config.url)
                if not metadata:
                    logger.debug(
                        "Failed to discover OAuth metadata for %s", self.server_name,
                    )
                    return None

                self._metadata = metadata

                token_endpoint = metadata.get("token_endpoint", "")
                if not token_endpoint:
                    logger.debug("No token endpoint in metadata for %s", self.server_name)
                    return None

                # Determine client_id
                client_info = await self.client_information()
                client_id = (
                    client_info.get("client_id")
                    if client_info
                    else os.environ.get("MCP_OAUTH_CLIENT_ID", "hare-mcp-client")
                )

                # Exchange refresh token
                response = await exchange_refresh_token(
                    token_endpoint=token_endpoint,
                    client_id=client_id,
                    refresh_token=refresh_token,
                    timeout=AUTH_REQUEST_TIMEOUT,
                )

                if response and response.get("access_token"):
                    tokens_dict = {
                        "access_token": response["access_token"],
                        "refresh_token": response.get("refresh_token"),
                        "expires_in": response.get("expires_in", 3600),
                        "scope": response.get("scope", ""),
                        "token_type": response.get("token_type", "Bearer"),
                    }
                    # Persist
                    await self.save_tokens(tokens_dict)
                    logger.debug("Token refresh successful for %s", self.server_name)
                    return tokens_dict

                logger.debug("Token refresh returned no tokens for %s", self.server_name)
                return None

            except TokenExchangeError as e:
                # Check if it's an invalid_grant error
                error_code = e.error
                if error_code in NONSTANDARD_INVALID_GRANT_ALIASES:
                    error_code = "invalid_grant"

                if error_code == "invalid_grant":
                    logger.debug(
                        "Token refresh failed with invalid_grant for %s: %s",
                        self.server_name, e.error_description,
                    )
                    # Check if another process refreshed
                    token_data = _load_mcp_oauth_entry(self.server_key)
                    if token_data:
                        expires_in = token_data.get("expiresAt", 0) - time.time()
                        if expires_in > PROACTIVE_REFRESH_WINDOW:
                            logger.debug(
                                "Another process refreshed tokens for %s, using those",
                                self.server_name,
                            )
                            return {
                                "access_token": token_data.get("accessToken", ""),
                                "refresh_token": token_data.get("refreshToken"),
                                "expires_in": int(max(0, expires_in)),
                                "scope": token_data.get("scope", ""),
                                "token_type": "Bearer",
                            }
                    # Clear stored tokens
                    await self.invalidate_credentials("tokens")
                    return None

                # Retry on network errors or server errors
                is_retryable = (
                    e.error == "network_error"
                    or e.status_code >= 500
                    or e.status_code == 429
                )
                if not is_retryable or attempt >= MAX_REFRESH_ATTEMPTS:
                    logger.debug(
                        "Token refresh failed for %s: %s",
                        self.server_name, e,
                    )
                    return None

                delay = 2 ** (attempt - 1)  # 1s, 2s, 4s
                logger.debug(
                    "Token refresh failed for %s, retrying in %ds (attempt %d/%d)",
                    self.server_name, delay, attempt, MAX_REFRESH_ATTEMPTS,
                )
                await asyncio.sleep(delay)

            except Exception as exc:
                logger.debug(
                    "Token refresh error for %s: %s", self.server_name, exc,
                )
                if attempt >= MAX_REFRESH_ATTEMPTS:
                    return None
                delay = 2 ** (attempt - 1)
                await asyncio.sleep(delay)

        return None

    # -- Step-up authentication ------------------------------------------------

    def mark_step_up_pending(self, scope: str) -> None:
        """Mark that a step-up authorization is needed for the given scope.

        Called when a 403 insufficient_scope response is detected.
        This causes tokens() to omit refresh_token, forcing the SDK to
        skip its (useless) refresh path and fall through to PKCE flow.

        RFC 6749 §6 forbids scope elevation via refresh, so refreshing
        would just return the same-scoped token and the retry would 403 again.
        """
        self._pending_step_up_scope = scope
        logger.debug("Marked step-up pending for %s: %s", self.server_name, scope)


# ---------------------------------------------------------------------------
# Step-up detection wrapper
# ---------------------------------------------------------------------------

def extract_step_up_scope_from_www_authenticate(www_auth_header: str) -> str | None:
    """Extract the scope from a WWW-Authenticate header with insufficient_scope.

    Matches both quoted and unquoted values per RFC 6750 §3.
    Example: Bearer error="insufficient_scope", scope="admin:read admin:write"
    """
    if "insufficient_scope" not in www_auth_header:
        return None
    # Match both quoted and unquoted values
    match = re.search(r'scope=(?:"([^"]+)"|([^\s,]+))', www_auth_header)
    if match:
        return match.group(1) or match.group(2)
    return None


# ---------------------------------------------------------------------------
# Loopback server for OAuth callback
# ---------------------------------------------------------------------------

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that captures the OAuth callback.

    Extracts ``code`` and ``state`` from query params, renders a simple
    HTML page, and signals completion via an asyncio event.
    """

    # Class-level references set by start_loopback_server
    callback_event: asyncio.Event | None = None
    callback_result: dict[str, str] = {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        error = query.get("error", [""])[0]
        error_description = query.get("error_description", [""])[0]

        result = {
            "code": code,
            "state": state,
            "error": error,
            "error_description": error_description,
        }

        if _OAuthCallbackHandler.callback_result is not ...:
            _OAuthCallbackHandler.callback_result = result

        if _OAuthCallbackHandler.callback_event:
            _OAuthCallbackHandler.callback_event.set()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()

        if error:
            body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Authorization Failed</title></head>
<body style="font-family:sans-serif;text-align:center;padding-top:3rem;">
<h1 style="color:#c00;">Authorization Failed</h1>
<p>{error_description or error}</p>
<p>You may close this window.</p>
</body></html>"""
        else:
            body = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Authorization Complete</title></head>
<body style="font-family:sans-serif;text-align:center;padding-top:3rem;">
<h1 style="color:#0a0;">Authorization Complete</h1>
<p>You may close this window and return to your terminal.</p>
</body></html>"""

        self.wfile.write(body.encode("utf-8"))
        # Don't log the request to stderr
        self.log_message = lambda fmt, *args: None


async def _run_loopback_server(
    port: int,
    event: asyncio.Event,
    timeout: float = 180.0,
) -> dict[str, str]:
    """Run a temporary loopback HTTP server on the given port.

    Waits for the OAuth callback or timeout. Returns the captured
    query parameters as a dict.
    """
    _OAuthCallbackHandler.callback_event = event
    _OAuthCallbackHandler.callback_result = {}

    server = HTTPServer(("127.0.0.1", port), _OAuthCallbackHandler)
    server.timeout = 1.0  # seconds between poll checks

    serve_task = None
    try:
        loop = asyncio.get_event_loop()

        async def _serve() -> None:
            while not event.is_set():
                server.handle_request()

        serve_task = asyncio.create_task(_serve())

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            _OAuthCallbackHandler.callback_result = {
                "error": "timeout",
                "error_description": "Authorization timed out after waiting for callback.",
            }
    finally:
        server.server_close()
        if serve_task is not None:
            serve_task.cancel()
            try:
                await serve_task
            except asyncio.CancelledError:
                pass

    return _OAuthCallbackHandler.callback_result


# ---------------------------------------------------------------------------
# High-level OAuth flow orchestrator
# ---------------------------------------------------------------------------

async def refresh_mcp_oauth_tokens_if_needed(
    config: McpSseServerConfig | McpHttpServerConfig,
    tokens: McpOAuthTokens | None,
) -> McpOAuthTokens | None:
    """Check whether tokens need refreshing and perform the refresh if so.

    Returns updated tokens, or None if no tokens are available / refresh fails.
    """
    if tokens is None:
        return None

    # No refresh token → can't refresh
    if not tokens.refresh_token:
        if tokens.is_expired:
            return None
        return tokens

    # Only refresh if expired
    if not tokens.is_expired:
        return tokens

    url = getattr(config, "url", "")
    if not url:
        return tokens

    metadata = discover_oauth_metadata(url)
    token_endpoint = metadata.get("token_endpoint", "")
    if not token_endpoint:
        return tokens

    client_id = os.environ.get("MCP_OAUTH_CLIENT_ID", "hare-mcp-client")

    try:
        response = await exchange_refresh_token(
            token_endpoint=token_endpoint,
            client_id=client_id,
            refresh_token=tokens.refresh_token,
        )
        tokens.update_from_response(response)
        return tokens
    except TokenExchangeError:
        return None
    except Exception:
        return None


async def start_mcp_oauth_flow(
    config: McpSseServerConfig | McpHttpServerConfig,
) -> McpOAuthTokens:
    """Run a full OAuth 2.0 authorization_code + PKCE flow for an MCP server.

    Steps:
    1. Discover OAuth metadata from the server URL
    2. Generate PKCE code_verifier / code_challenge and state
    3. Find an available port and start a loopback redirect server
    4. Build and display (or open) the authorization URL
    5. Wait for the callback on the loopback server
    6. Exchange the authorization code for tokens
    7. Return McpOAuthTokens

    Raises TokenExchangeError or RuntimeError on failure.
    """
    url = getattr(config, "url", "")
    if not url:
        raise RuntimeError("Cannot start OAuth flow: no URL in server config")

    # 1. Discover OAuth metadata
    metadata = discover_oauth_metadata(url)
    authorization_endpoint = metadata.get("authorization_endpoint", "")
    token_endpoint = metadata.get("token_endpoint", "")

    if not authorization_endpoint or not token_endpoint:
        raise RuntimeError(
            f"OAuth metadata incomplete for {url}: "
            f"auth_endpoint={authorization_endpoint!r}, token_endpoint={token_endpoint!r}"
        )

    # 2. PKCE and state
    code_verifier, code_challenge = generate_pkce_pair()
    state = generate_state()

    # 3. Find port and build redirect URI
    port = await find_available_port()
    redirect_uri = build_redirect_uri(port)

    # 4. Client ID from env or config headers
    client_id = os.environ.get("MCP_OAUTH_CLIENT_ID", "hare-mcp-client")
    headers = getattr(config, "headers", {}) or {}
    if "x-oauth-client-id" in {k.lower() for k in headers}:
        for k, v in headers.items():
            if k.lower() == "x-oauth-client-id":
                client_id = v
                break

    # 5. Build authorization URL
    scopes = _build_default_scopes()
    auth_url = build_oauth_authorization_url(
        authorization_endpoint=authorization_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        code_challenge=code_challenge,
        state=state,
    )

    # 6. Start loopback server
    callback_event = asyncio.Event()
    loopback_task = asyncio.create_task(
        _run_loopback_server(port, callback_event, timeout=180.0)
    )

    # Print the URL for the user (or open browser if possible)
    ide = detect_ide_for_oauth()
    if ide:
        print(
            f"\n[MCP OAuth] Detected IDE: {ide}. "
            f"Open this URL in your browser to authorize:\n{auth_url}\n",
            file=sys.stderr,
        )
    else:
        print(
            f"\n[MCP OAuth] Open this URL to authorize:\n{auth_url}\n",
            file=sys.stderr,
        )

    # 7. Wait for callback
    callback_result = await loopback_task

    error = callback_result.get("error", "")
    if error:
        raise RuntimeError(
            f"OAuth authorization failed: {error} - "
            f"{callback_result.get('error_description', '')}"
        )

    code = callback_result.get("code", "")
    returned_state = callback_result.get("state", "")

    if not code:
        raise RuntimeError("No authorization code received in callback")

    # Verify state to prevent CSRF
    if returned_state and returned_state != state:
        raise RuntimeError(
            f"OAuth state mismatch — possible CSRF attack. "
            f"Expected {state[:8]}..., got {returned_state[:8]}..."
        )

    # 8. Exchange code for tokens
    raw_response = await exchange_code_for_tokens(
        token_endpoint=token_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code=code,
        code_verifier=code_verifier,
    )

    tokens = McpOAuthTokens(access_token=raw_response.get("access_token", ""))
    tokens.update_from_response(raw_response)

    if not tokens.access_token:
        raise RuntimeError("Token endpoint did not return an access_token")

    return tokens


async def perform_mcp_oauth_flow(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
    *,
    on_authorization_url: Callable[[str], None] | None = None,
    skip_browser_open: bool = False,
    on_waiting_for_callback: Callable[[Callable[[str], None]], None] | None = None,
    abort_signal: asyncio.Event | None = None,
) -> OAuthClientProvider:
    """Run the full MCP OAuth flow with SDK-compatible provider pattern.

    This is the high-level entry point for MCP OAuth. It:
    1. Clears existing credentials for a fresh flow
    2. Creates an OAuthClientProvider
    3. Discovers OAuth metadata
    4. Starts a loopback callback server
    5. Builds and displays the authorization URL
    6. Waits for the callback (or manual paste)
    7. Returns the provider (tokens are persisted internally)

    Args:
        server_name: Human-readable server name for logging/storage.
        server_config: MCP server config (SSE or HTTP).
        on_authorization_url: Called with the URL to show the user.
        skip_browser_open: If True, don't try to open the browser.
        on_waiting_for_callback: Called with a ``submit(callback_url)``
            function for manual callback URL paste support.
        abort_signal: Set this event to cancel the flow.

    Returns:
        The OAuthClientProvider with persisted tokens.

    Raises:
        AuthenticationCancelledError: User cancelled or abort signal fired.
        CallbackTimeoutError: No callback received within the timeout.
        CallbackStateMismatchError: State parameter mismatch.
        PortUnavailableError: Could not bind the callback port.
        OAuthError: Other OAuth errors.
    """
    # Check for abort before starting
    if abort_signal and abort_signal.is_set():
        raise AuthenticationCancelledError()

    # Check for cached step-up scope before clearing tokens
    server_key = get_server_key(server_name, server_config)
    cached_entry = _load_mcp_oauth_entry(server_key)
    cached_step_up_scope = cached_entry.get("stepUpScope") if cached_entry else None
    cached_resource_metadata_url = (
        cached_entry.get("discoveryState", {}).get("resourceMetadataUrl")
        if cached_entry
        else None
    )

    # Clear existing stored credentials to ensure fresh client registration
    clear_server_tokens_from_storage(server_name, server_config)

    # Determine callback port
    headers = getattr(server_config, "headers", {}) or {}
    configured_callback_port = None
    for k, v in headers.items():
        if k.lower() == "x-oauth-callback-port":
            try:
                configured_callback_port = int(v)
            except (ValueError, TypeError):
                pass
            break

    port = (
        configured_callback_port
        if configured_callback_port is not None
        else await find_available_port()
    )
    redirect_uri = build_redirect_uri(port)
    logger.debug(
        "Using redirect port %d for %s%s",
        port, server_name, " (from config)" if configured_callback_port else "",
    )

    # Create provider
    provider = OAuthClientProvider(
        server_name=server_name,
        server_config=server_config,
        redirect_uri=redirect_uri,
        handle_redirection=True,
        on_authorization_url=on_authorization_url,
        skip_browser_open=skip_browser_open,
    )

    # Fetch OAuth metadata for scope information
    try:
        metadata = discover_oauth_metadata(server_config.url)
        if metadata:
            provider.set_metadata(metadata)
            logger.debug(
                "Fetched OAuth metadata for %s with scope: %s",
                server_name, get_scope_from_metadata(metadata) or "NONE",
            )
    except Exception as exc:
        logger.debug("Failed to fetch OAuth metadata for %s: %s", server_name, exc)

    # Generate state and PKCE
    oauth_state = await provider.state()
    code_verifier, code_challenge = generate_pkce_pair()
    await provider.save_code_verifier(code_verifier)

    # Determine client_id
    client_id = os.environ.get("MCP_OAUTH_CLIENT_ID", "hare-mcp-client")
    for k, v in headers.items():
        if k.lower() == "x-oauth-client-id":
            client_id = v
            break

    # Build authorization URL
    scopes = _build_default_scopes()
    if cached_step_up_scope:
        scopes = cached_step_up_scope.split()
    auth_url = build_oauth_authorization_url(
        authorization_endpoint=metadata.get("authorization_endpoint", ""),
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        code_challenge=code_challenge,
        state=oauth_state,
    )

    # Start loopback server and wait for callback
    callback_event = asyncio.Event()

    async def _handle_callback() -> str:
        result = await _run_loopback_server(port, callback_event, timeout=CALLBACK_TIMEOUT)

        error = result.get("error", "")
        if error:
            if error == "timeout":
                raise CallbackTimeoutError()
            raise OAuthError(
                f"OAuth authorization failed: {error} - {result.get('error_description', '')}",
                error_code=error,
            )

        code = result.get("code", "")
        returned_state = result.get("state", "")

        if not code:
            raise OAuthError("No authorization code received in callback", error_code="no_code")

        # Verify state to prevent CSRF
        if returned_state and returned_state != oauth_state:
            raise CallbackStateMismatchError()

        return code

    # Allow manual callback URL paste
    if on_waiting_for_callback is not None:
        def _manual_submit(callback_url: str) -> None:
            """Process a manually pasted callback URL."""
            if callback_event.is_set():
                return
            try:
                parsed = urllib.parse.urlparse(callback_url)
                query = urllib.parse.parse_qs(parsed.query)
                error = query.get("error", [None])[0]
                if error:
                    desc = query.get("error_description", [None])[0] or ""
                    _OAuthCallbackHandler.callback_result = {
                        "error": error,
                        "error_description": desc,
                    }
                    callback_event.set()
                    return
                code = query.get("code", [None])[0]
                state_val = query.get("state", [None])[0]
                if not code:
                    return  # Not a valid callback URL, ignore
                _OAuthCallbackHandler.callback_result = {
                    "code": code,
                    "state": state_val or "",
                    "error": "",
                    "error_description": "",
                }
                callback_event.set()
            except Exception:
                pass  # Ignore invalid URLs so the user can retry

        on_waiting_for_callback(_manual_submit)

    # Set up abort listener
    if abort_signal:
        async def _wait_abort() -> None:
            await abort_signal.wait()
            callback_event.set()
            _OAuthCallbackHandler.callback_result = {
                "error": "cancelled",
                "error_description": "Authentication was cancelled",
            }

        asyncio.ensure_future(_wait_abort())

    # Display the auth URL to the user
    if on_authorization_url:
        on_authorization_url(auth_url)
    else:
        print(f"\n[MCP OAuth] Open this URL to authorize:\n{auth_url}\n", file=sys.stderr)

    if not skip_browser_open:
        _open_browser(auth_url)

    # Wait for the callback
    authorization_code = await _handle_callback()

    # Token exchange
    token_endpoint = metadata.get("token_endpoint", "")
    if not token_endpoint:
        raise OAuthError("No token endpoint in OAuth metadata", error_code="no_token_endpoint")

    raw_response = await exchange_code_for_tokens(
        token_endpoint=token_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code=authorization_code,
        code_verifier=code_verifier,
    )

    if not raw_response.get("access_token"):
        raise OAuthError(
            "Token endpoint did not return an access_token",
            error_code="no_access_token",
        )

    # Save tokens via the provider
    tokens_dict = {
        "access_token": raw_response["access_token"],
        "refresh_token": raw_response.get("refresh_token"),
        "expires_in": raw_response.get("expires_in", 3600),
        "scope": raw_response.get("scope", ""),
        "token_type": raw_response.get("token_type", "Bearer"),
    }
    await provider.save_tokens(tokens_dict)

    # Also save client_id for future operations
    server_key = get_server_key(server_name, server_config)
    existing = _load_mcp_oauth_entry(server_key) or {}
    existing["clientId"] = client_id
    _save_mcp_oauth_entry(server_key, existing)

    logger.info("MCP OAuth flow completed successfully for %s", server_name)
    return provider


# ---------------------------------------------------------------------------
# Convenience: has discovery but no token check
# ---------------------------------------------------------------------------

def has_mcp_oauth_tokens(
    server_name: str,
    server_config: McpSseServerConfig | McpHttpServerConfig,
) -> bool:
    """Check if we have valid (non-expired) tokens for an MCP server."""
    server_key = get_server_key(server_name, server_config)
    entry = _load_mcp_oauth_entry(server_key)
    if not entry:
        return False
    access_token = entry.get("accessToken", "")
    expires_at = entry.get("expiresAt", 0)
    if not access_token:
        return False
    if expires_at > 0 and time.time() > expires_at - TOKEN_EXPIRY_BUFFER:
        # Expired — only valid if we have a refresh token
        return bool(entry.get("refreshToken"))
    return True
