"""
Bridge auth/URL resolution.

Port of: src/bridge/bridgeConfig.ts

Two layers:
  - *Override() returns the ant-only env var (or None)
  - Non-override versions fall through to the real OAuth store/config.
"""

from __future__ import annotations

import os
from typing import Any, Optional


def _is_ant_user() -> bool:
    return os.environ.get("USER_TYPE") == "ant"


def get_bridge_token_override() -> Optional[str]:
    """Ant-only dev override: CLAUDE_BRIDGE_OAUTH_TOKEN, else None."""
    if _is_ant_user():
        token = os.environ.get("CLAUDE_BRIDGE_OAUTH_TOKEN")
        if token:
            return token
    return None


def get_bridge_base_url_override() -> Optional[str]:
    """Ant-only dev override: CLAUDE_BRIDGE_BASE_URL, else None."""
    if _is_ant_user():
        url = os.environ.get("CLAUDE_BRIDGE_BASE_URL")
        if url:
            return url
    return None


def get_bridge_access_token(get_claude_ai_oauth_tokens: Any = None) -> Optional[str]:
    """Access token for bridge API calls.

    Priority: dev override, then OAuth keychain from claude.ai.
    Returns None if not logged in.
    """
    override = get_bridge_token_override()
    if override:
        return override

    if get_claude_ai_oauth_tokens:
        tokens = get_claude_ai_oauth_tokens()
        if tokens and tokens.get("accessToken"):
            return tokens["accessToken"]

    return None


def get_bridge_base_url(get_oauth_config: Any = None) -> str:
    """Base URL for bridge API calls.

    Priority: dev override, then production OAuth config.
    Always returns a URL.
    """
    override = get_bridge_base_url_override()
    if override:
        return override

    if get_oauth_config:
        config = get_oauth_config()
        return config.get("BASE_API_URL", "https://api.claude.ai")

    return "https://api.claude.ai"
