"""
Runtime checks for bridge mode entitlement.

Port of: src/bridge/bridgeEnabled.ts

Remote Control requires a claude.ai subscription (OAuth token).
GrowthBook gates control rollout and per-feature toggles.
"""

from __future__ import annotations

import os
from typing import Any, Optional


def _get_feature_value_cached(
    key: str, default: Any, context: Optional[dict[str, Any]] = None
) -> Any:
    ctx = context or {}
    getter = ctx.get("get_feature_value_cached_may_be_stale")
    if getter:
        return getter(key, default)
    return default


def _check_gate_cached_or_blocking(
    key: str, context: Optional[dict[str, Any]] = None
) -> Any:
    ctx = context or {}
    checker = ctx.get("check_gate_cached_or_blocking")
    if checker:
        return checker(key)
    return _get_feature_value_cached(key, False, context)


def _is_claude_ai_subscriber(context: Optional[dict[str, Any]] = None) -> bool:
    ctx = context or {}
    checker = ctx.get("is_claude_ai_subscriber")
    if checker:
        try:
            return checker()
        except Exception:
            return False
    return False


def _has_profile_scope(context: Optional[dict[str, Any]] = None) -> bool:
    ctx = context or {}
    checker = ctx.get("has_profile_scope")
    if checker:
        try:
            return checker()
        except Exception:
            return False
    return False


def _get_oauth_account_info(
    context: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    ctx = context or {}
    getter = ctx.get("get_oauth_account_info")
    if getter:
        try:
            return getter()
        except Exception:
            return None
    return None


def is_bridge_enabled(context: Optional[dict[str, Any]] = None) -> bool:
    """Runtime check for bridge mode entitlement."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature and has_feature("BRIDGE_MODE"):
        return _is_claude_ai_subscriber(context) and _get_feature_value_cached(
            "tengu_ccr_bridge", False, context
        )
    return False


async def is_bridge_enabled_blocking(context: Optional[dict[str, Any]] = None) -> bool:
    """Blocking entitlement check — awaits GrowthBook init if needed."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature and has_feature("BRIDGE_MODE"):
        return _is_claude_ai_subscriber(context) and bool(
            await _check_gate_cached_or_blocking("tengu_ccr_bridge", context)
        )
    return False


async def get_bridge_disabled_reason(
    context: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Diagnostic message for why Remote Control is unavailable, or None if enabled."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")

    if not has_feature or not has_feature("BRIDGE_MODE"):
        return "Remote Control is not available in this build."

    if not _is_claude_ai_subscriber(context):
        return (
            "Remote Control requires a claude.ai subscription. "
            "Run `claude auth login` to sign in with your claude.ai account."
        )

    if not _has_profile_scope(context):
        return (
            "Remote Control requires a full-scope login token. "
            "Long-lived tokens are limited to inference-only for security reasons. "
            "Run `claude auth login` to use Remote Control."
        )

    account = _get_oauth_account_info(context)
    if not account or not account.get("organizationUuid"):
        return (
            "Unable to determine your organization for Remote Control eligibility. "
            "Run `claude auth login` to refresh your account information."
        )

    if not await _check_gate_cached_or_blocking("tengu_ccr_bridge", context):
        return "Remote Control is not yet enabled for your account."

    return None


def is_env_less_bridge_enabled(context: Optional[dict[str, Any]] = None) -> bool:
    """Check if env-less (v2) REPL bridge path is enabled."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature and has_feature("BRIDGE_MODE"):
        return _get_feature_value_cached("tengu_bridge_repl_v2", False, context)
    return False


def is_cse_shim_enabled(context: Optional[dict[str, Any]] = None) -> bool:
    """Kill-switch for cse_* -> session_* client-side retag shim."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature and has_feature("BRIDGE_MODE"):
        return _get_feature_value_cached(
            "tengu_bridge_repl_v2_cse_shim_enabled", True, context
        )
    return True


def check_bridge_min_version(
    current_version: str = "0.0.0", context: Optional[dict[str, Any]] = None
) -> Optional[str]:
    """Check if current CLI version meets minimum for v1 bridge."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature and has_feature("BRIDGE_MODE"):
        get_dynamic_config = ctx.get("get_dynamic_config_cached_may_be_stale")
        if get_dynamic_config:
            config = get_dynamic_config(
                "tengu_bridge_min_version", {"minVersion": "0.0.0"}
            )
        else:
            config = {"minVersion": "0.0.0"}
        min_version = config.get("minVersion", "0.0.0")
        if min_version and _lt_version(current_version, min_version):
            return (
                f"Your version ({current_version}) is too old for Remote Control. "
                f"Version {min_version} or higher is required. Run `claude update`."
            )
    return None


def get_ccr_auto_connect_default(context: Optional[dict[str, Any]] = None) -> bool:
    """Default for remoteControlAtStartup when user hasn't explicitly set it."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature and has_feature("CCR_AUTO_CONNECT"):
        return _get_feature_value_cached("tengu_cobalt_harbor", False, context)
    return False


def is_ccr_mirror_enabled(context: Optional[dict[str, Any]] = None) -> bool:
    """Opt-in CCR mirror mode."""
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature and has_feature("CCR_MIRROR"):
        env_val = os.environ.get("CLAUDE_CODE_CCR_MIRROR", "").lower()
        if env_val in ("1", "true", "yes"):
            return True
        return _get_feature_value_cached("tengu_ccr_mirror", False, context)
    return False


def _lt_version(a: str, b: str) -> bool:
    """Simple semver compare: True if a < b."""
    try:
        pa = [int(x) for x in a.split(".")]
        pb = [int(x) for x in b.split(".")]
        while len(pa) < 3:
            pa.append(0)
        while len(pb) < 3:
            pb.append(0)
        return pa < pb
    except (ValueError, TypeError):
        return False
