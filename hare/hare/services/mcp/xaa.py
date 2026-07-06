"""
Cross-App Access (XAA) token exchange for MCP OAuth.

Port of: src/services/mcp/xaa.ts

Handles exchanging refresh tokens across applications, enabling MCP
servers to authenticate via OAuth in browser/desktop contexts.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional


class XaaTokenExchangeError(Exception):
    """Error during cross-app access token exchange."""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class XaaIdpLoginError(Exception):
    """Error during IdP login flow."""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


async def perform_cross_app_access(refresh_token: str, audience: str) -> dict[str, str]:
    """Exchange a refresh token for an access token targeting a specific audience.

    This is used when an MCP server requires OAuth tokens for a different
    application scope than the current CLI session.

    Returns dict with access_token, token_type, expires_in, scope.
    """
    if not refresh_token or not audience:
        raise XaaTokenExchangeError("refresh_token and audience are required", 400)

    body = json.dumps({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": refresh_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:refresh_token",
        "audience": audience,
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
    }).encode("utf-8")

    import asyncio
    def _sync_request() -> dict[str, Any]:
        req = urllib.request.Request(
            "https://console.anthropic.com/oauth/token",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise XaaTokenExchangeError(f"Token exchange failed (HTTP {e.code}): {error_body}", e.code)

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _sync_request)
        return {
            "access_token": result.get("access_token", ""),
            "token_type": result.get("token_type", "bearer"),
            "expires_in": str(result.get("expires_in", 3600)),
            "scope": result.get("scope", ""),
        }
    except XaaTokenExchangeError:
        raise
    except Exception as e:
        raise XaaTokenExchangeError(str(e))
