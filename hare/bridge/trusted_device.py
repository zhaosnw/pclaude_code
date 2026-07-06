"""
Trusted device token management for bridge (remote-control) sessions.

Port of: src/bridge/trustedDevice.ts

Bridge sessions have SecurityTier=ELEVATED. This manages the
X-Trusted-Device-Token used for elevated auth bridge calls.
Uses GrowthBook gate: tengu_sessions_elevated_auth_enforcement.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any, Optional

TRUSTED_DEVICE_GATE = "tengu_sessions_elevated_auth_enforcement"

_TOKEN_CACHE: Optional[str] = None
_CACHE_VALID = False


def _get_token_file() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "trusted_device.json")


def _is_gate_enabled(context: Optional[dict[str, Any]] = None) -> bool:
    """Check GrowthBook gate for trusted device enforcement."""
    ctx = context or {}
    getter = ctx.get("get_feature_value_cached_may_be_stale")
    if getter:
        return bool(getter(TRUSTED_DEVICE_GATE, False))
    return False


def _read_stored_token() -> Optional[str]:
    """Read stored token from cache or disk. Memoized equivalent."""
    global _TOKEN_CACHE, _CACHE_VALID

    # Env var takes precedence
    env_token = os.environ.get("CLAUDE_TRUSTED_DEVICE_TOKEN")
    if env_token:
        return env_token

    if _CACHE_VALID:
        return _TOKEN_CACHE

    token_file = _get_token_file()
    try:
        with open(token_file, "r") as f:
            data = json.load(f)
        _TOKEN_CACHE = data.get("trustedDeviceToken")
    except (OSError, json.JSONDecodeError):
        _TOKEN_CACHE = None

    _CACHE_VALID = True
    return _TOKEN_CACHE


def get_trusted_device_token(context: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Get trusted device token for bridge API calls."""
    if not _is_gate_enabled(context):
        return None
    return _read_stored_token()


def clear_trusted_device_token_cache() -> None:
    """Clear memo cache (called after enrollment or logout)."""
    global _TOKEN_CACHE, _CACHE_VALID
    _TOKEN_CACHE = None
    _CACHE_VALID = False


def clear_trusted_device_token() -> None:
    """Clear stored token from disk and cache."""
    token_file = _get_token_file()
    try:
        data = {}
        try:
            with open(token_file, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        if "trustedDeviceToken" in data:
            del data["trustedDeviceToken"]
            with open(token_file, "w") as f:
                json.dump(data, f)
    except OSError:
        pass
    clear_trusted_device_token_cache()


async def enroll_trusted_device(
    get_claude_ai_oauth_tokens: Any = None,
    get_oauth_config: Any = None,
    http_post: Any = None,
    context: Optional[dict[str, Any]] = None,
) -> None:
    """Enroll this device via POST /api/auth/trusted_devices.

    Must be called during /login (server gates on account_session.created_at < 10min).
    Best-effort: does not block login flow on failure.
    """
    try:
        ctx = context or {}
        check_gate = ctx.get("check_gate_cached_or_blocking")
        if check_gate:
            if not await check_gate(TRUSTED_DEVICE_GATE):
                return

        if os.environ.get("CLAUDE_TRUSTED_DEVICE_TOKEN"):
            return

        if not get_claude_ai_oauth_tokens:
            return
        tokens = get_claude_ai_oauth_tokens()
        access_token = tokens.get("accessToken") if tokens else None
        if not access_token:
            return

        if not get_oauth_config:
            return
        oauth_cfg = get_oauth_config()
        base_url = oauth_cfg.get("BASE_API_URL", "")

        display_name = f"Claude Code on {socket.gethostname()} · {os.name}"

        if not http_post:
            return

        response = await http_post(
            f"{base_url}/api/auth/trusted_devices",
            json={"display_name": display_name},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        if response.get("status") not in (200, 201):
            return

        data = response.get("data", {})
        token = data.get("device_token") if isinstance(data, dict) else None
        if not token or not isinstance(token, str):
            return

        # Persist to disk
        token_file = _get_token_file()
        os.makedirs(os.path.dirname(token_file), exist_ok=True)
        stored = {}
        try:
            with open(token_file, "r") as f:
                stored = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        stored["trustedDeviceToken"] = token
        with open(token_file, "w") as f:
            json.dump(stored, f)

        clear_trusted_device_token_cache()
    except Exception:
        pass
