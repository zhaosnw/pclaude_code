"""
Low-level OAuth2 client (authorization URL, token exchange, refresh).

Port of: src/services/oauth/client.ts

Handles OAuth2 Authorization Code flow with PKCE support.
Includes token exchange, token refresh, and token persistence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import base64
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "bearer"
    scope: str = ""
    organization_uuid: str | None = None


def generate_code_verifier(length: int = 64) -> str:
    """Generate a cryptographically random PKCE S256 code verifier."""
    return secrets.token_urlsafe(length)[:128]


def generate_code_challenge(verifier: str) -> str:
    """Generate a PKCE S256 code challenge from a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorization_url(
    authorize_url: str, client_id: str, redirect_uri: str,
    state: str, code_challenge: str | None = None,
) -> str:
    """Build the OAuth authorization URL for browser-based login."""
    from urllib.parse import urlencode
    q: dict[str, str] = {
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "state": state,
    }
    if code_challenge:
        q["code_challenge"] = code_challenge
        q["code_challenge_method"] = "S256"
    return f"{authorize_url}?{urlencode(q)}"


def _make_token_request(token_url: str, body: dict[str, str]) -> dict[str, Any]:
    """Make a synchronous token endpoint request."""
    req = urllib.request.Request(
        token_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OAuth token request failed (HTTP {e.code}): {error_body}") from e


async def exchange_code_for_tokens(
    token_url: str, client_id: str, code: str, redirect_uri: str,
    code_verifier: str | None = None,
) -> TokenResponse:
    """Exchange an authorization code for OAuth tokens with PKCE support."""
    body = {
        "grant_type": "authorization_code", "client_id": client_id,
        "code": code, "redirect_uri": redirect_uri,
    }
    if code_verifier:
        body["code_verifier"] = code_verifier
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _make_token_request(token_url, body)
    )
    return TokenResponse(
        access_token=result.get("access_token", ""),
        refresh_token=result.get("refresh_token"),
        expires_in=result.get("expires_in"),
        token_type=result.get("token_type", "bearer"),
        scope=result.get("scope", ""),
        organization_uuid=result.get("organization_uuid"),
    )


async def refresh_access_token(
    token_url: str, client_id: str, refresh_token: str,
) -> TokenResponse:
    """Refresh an expired access token using a refresh token."""
    body = {
        "grant_type": "refresh_token", "client_id": client_id,
        "refresh_token": refresh_token,
    }
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _make_token_request(token_url, body)
    )
    return TokenResponse(
        access_token=result.get("access_token", ""),
        refresh_token=result.get("refresh_token", refresh_token),
        expires_in=result.get("expires_in"),
        token_type=result.get("token_type", "bearer"),
        scope=result.get("scope", ""),
        organization_uuid=result.get("organization_uuid"),
    )


def save_tokens(tokens: TokenResponse, path: str = "") -> str:
    """Persist OAuth tokens to disk. Returns the path used."""
    if not path:
        path = os.path.expanduser("~/.claude/oauth_tokens.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_in": tokens.expires_in,
        "token_type": tokens.token_type,
        "scope": tokens.scope,
        "organization_uuid": tokens.organization_uuid,
        "saved_at": time.time(),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def load_tokens(path: str = "") -> Optional[TokenResponse]:
    """Load persisted OAuth tokens, returning None if expired or missing."""
    if not path:
        path = os.path.expanduser("~/.claude/oauth_tokens.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        expires_in = data.get("expires_in")
        saved_at = data.get("saved_at", 0)
        if expires_in and saved_at:
            elapsed = time.time() - saved_at
            if elapsed > expires_in - 60:
                return None
        return TokenResponse(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_in=expires_in,
            token_type=data.get("token_type", "bearer"),
            scope=data.get("scope", ""),
            organization_uuid=data.get("organization_uuid"),
        )
    except Exception:
        return None


def clear_tokens(path: str = "") -> None:
    """Remove persisted OAuth tokens from disk."""
    if not path:
        path = os.path.expanduser("~/.claude/oauth_tokens.json")
    if os.path.isfile(path):
        os.remove(path)
