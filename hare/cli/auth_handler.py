"""
CLI handler for auth subcommand — login, logout, status, setup-token.

Port of: src/cli/handlers/auth.ts
"""

from __future__ import annotations

import os
from typing import Any


async def handle_auth_command(args: dict[str, Any]) -> None:
    """Handle the 'auth' CLI subcommand.

    Actions: login, logout, status, setup-token
    """
    action = args.get("action", "status")

    if action == "login":
        await _auth_login(args)
    elif action == "logout":
        await _auth_logout(args)
    elif action == "status":
        _auth_status(args)
    elif action == "setup-token":
        _auth_setup_token(args)
    else:
        print(f"Unknown auth action: {action}")


async def _auth_login(args: dict[str, Any]) -> None:
    """Initiate OAuth login flow.

    In headless mode, guides user to set ANTHROPIC_API_KEY.
    For interactive OAuth, use the Claude Code CLI.
    """
    # Check for env var override
    env_token = os.environ.get("ANTHROPIC_REFRESH_TOKEN") or os.environ.get(
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"
    )
    if env_token:
        print("OAuth refresh token found in environment. Already authenticated.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        print("Already authenticated via ANTHROPIC_API_KEY.")
        return

    # Guide to interactive login
    print("To log in with your claude.ai account:\n")
    print("  1. Run: claude login")
    print("  2. Follow the browser-based OAuth flow")
    print("")
    print("For API key authentication:")
    print("  export ANTHROPIC_API_KEY=sk-ant-...")
    print("")
    print("Get your API key at: https://console.anthropic.com/")


async def _auth_logout(args: dict[str, Any]) -> None:
    """Clear stored credentials and session tokens."""
    cleared = False

    # Clear API key env var hint
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is set via environment variable.")
        print("Unset it to log out:  unset ANTHROPIC_API_KEY")
        cleared = True

    # Try clearing stored credentials
    try:
        cred_file = os.path.join(os.path.expanduser("~"), ".claude", "credentials.json")
        if os.path.exists(cred_file):
            os.remove(cred_file)
            print("Stored credentials cleared.")
            cleared = True
    except OSError:
        pass

    # Try clearing OAuth tokens from keychain equivalent
    try:
        token_file = os.path.join(
            os.path.expanduser("~"), ".claude", "oauth_tokens.json"
        )
        if os.path.exists(token_file):
            os.remove(token_file)
            print("Stored OAuth tokens cleared.")
            cleared = True
    except OSError:
        pass

    if not cleared:
        print("No stored credentials found. Not logged in.")


def _auth_status(args: dict[str, Any]) -> None:
    """Show authentication status."""
    json_output = args.get("json", False)

    status: dict[str, Any] = {
        "authenticated": False,
        "method": None,
        "account": None,
    }

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        status["authenticated"] = True
        status["method"] = "api_key"
        status["api_key_prefix"] = api_key[:11] + "..."

    # Check OAuth
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if oauth_token:
        status["authenticated"] = True
        status["method"] = "oauth"

    if json_output:
        import json as _json

        print(_json.dumps(status, indent=2))
    else:
        if status["authenticated"]:
            print(f"Authenticated via {status['method']}.")
        else:
            print("Not authenticated.")
            print("Set ANTHROPIC_API_KEY environment variable or run 'claude login'.")


def _auth_setup_token(args: dict[str, Any]) -> None:
    """Handle setup-token for long-lived API tokens."""
    print(
        "Setup-token: Use 'claude setup-token' in the CLI to create a long-lived token."
    )
    print("Long-lived tokens are limited to inference-only for security reasons.")
    print("For full access, use 'claude auth login'.")
