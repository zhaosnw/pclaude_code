"""
MCP XAA IdP management — configure IdP connection for XAA (SEP-990) servers.

Port of: src/commands/mcp/xaaIdpCommand.ts (266 lines)

Manages 'claude mcp xaa setup/clear/auth' subcommands.
The IdP connection is user-level: configure once, all XAA-enabled MCP servers reuse it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def validate_xaa_setup(
    issuer: str = "",
    client_id: str = "",
    client_secret: bool = False,
    callback_port: int | None = None,
) -> dict[str, Any]:
    """Validate 'claude mcp xaa setup' arguments BEFORE any writes.

    Raises ValueError on invalid input. Failing early prevents leaving
    keychain in an inconsistent state with partial settings.
    """
    errors: list[str] = []

    # Validate issuer URL
    try:
        parsed = urlparse(issuer)
        if parsed.scheme not in ("https", "http"):
            raise ValueError(f'Error: --issuer must be a valid URL (got "{issuer}")')
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "[::1]",
        ):
            raise ValueError(
                f'Error: --issuer must use https:// (got "{parsed.scheme}://{parsed.hostname}")'
            )
    except ValueError:
        raise ValueError(f'Error: --issuer must be a valid URL (got "{issuer}")')

    if not client_id:
        errors.append("--client-id is required")

    if callback_port is not None and callback_port <= 0:
        errors.append("Error: --callback-port must be a positive integer")

    if client_secret:
        import os

        if not os.environ.get("MCP_XAA_IDP_CLIENT_SECRET"):
            errors.append(
                "Error: --client-secret requires MCP_XAA_IDP_CLIENT_SECRET env var"
            )

    if errors:
        raise ValueError("\n".join(errors))

    return {
        "issuer": issuer,
        "clientId": client_id,
        "callbackPort": callback_port,
        "hasClientSecret": client_secret,
    }


def validate_xaa_clear(issuer: str | None = None) -> dict[str, Any]:
    """Validate 'claude mcp xaa clear' arguments."""
    if not issuer:
        raise ValueError("Error: --issuer is required for xaa clear")
    try:
        parsed = urlparse(issuer)
        if parsed.scheme not in ("https", "http"):
            raise ValueError(f'Error: --issuer must be a valid URL (got "{issuer}")')
    except ValueError:
        raise ValueError(f'Error: --issuer must be a valid URL (got "{issuer}")')
    return {"issuer": issuer}


def validate_xaa_auth() -> dict[str, Any]:
    """Validate 'claude mcp xaa auth' — check IdP is configured first."""
    import os
    import json

    settings_path = os.path.expanduser("~/.claude/settings.json")
    settings = {}
    try:
        if os.path.exists(settings_path):
            with open(settings_path) as f:
                settings = json.load(f)
    except Exception:
        pass

    xaa_idp = settings.get("xaaIdp", {})
    if not xaa_idp.get("issuer") or not xaa_idp.get("clientId"):
        raise ValueError(
            "Error: Run 'claude mcp xaa setup --issuer <url> --client-id <id>' first"
        )
    return {"xaaIdp": xaa_idp}
