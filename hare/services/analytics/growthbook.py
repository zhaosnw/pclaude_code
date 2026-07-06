"""
GrowthBook feature flag integration.

Port of: src/services/analytics/growthbook.ts

Simplified implementation using environment variables and local
configuration for feature flag evaluation. Supports overrides,
caching, and blocking gate checks.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional, TypeVar

T = TypeVar("T")

_feature_overrides: dict[str, Any] = {}
_feature_cache: dict[str, tuple[Any, float]] = {}
_initialized = False
CACHE_TTL_SECONDS = 300  # 5 minutes


def init_growthbook(attributes: Optional[dict[str, Any]] = None) -> None:
    """Initialize local feature-value access."""
    global _initialized
    _initialized = True
    # Load feature overrides from environment
    for key, val in os.environ.items():
        if key.startswith("GROWTHBOOK_FEATURE_"):
            feature_key = key[19:].lower()
            _feature_overrides[feature_key] = _parse_env_value(val)
        elif key.startswith("CLAUDE_CODE_FEATURE_"):
            feature_key = key[18:].lower()
            _feature_overrides[feature_key] = _parse_env_value(val)


def set_feature_override(key: str, value: Any) -> None:
    """Override a feature flag value (for testing)."""
    _feature_overrides[key] = value


def clear_feature_overrides() -> None:
    """Clear all overrides."""
    _feature_overrides.clear()
    _feature_cache.clear()


def get_feature_value(key: str, default: T = False) -> Any | T:
    """Get a feature flag value with caching.

    Checks order: overrides → env var → cache → default.
    """
    # Check overrides
    if key in _feature_overrides:
        return _feature_overrides[key]

    # Check env vars directly
    env_key = f"CLAUDE_CODE_FEATURE_{key.upper()}"
    if env_key in os.environ:
        return _parse_env_value(os.environ[env_key])

    # Check cache
    cached = _feature_cache.get(key)
    if cached is not None:
        val, ts = cached
        if time.time() - ts < CACHE_TTL_SECONDS:
            return val

    return default


def get_feature_value_cached_may_be_stale(key: str, default: T = False) -> Any | T:
    """Get a feature value, returning cached value even if stale."""
    if key in _feature_overrides:
        return _feature_overrides[key]
    cached = _feature_cache.get(key)
    if cached is not None:
        return cached[0]
    return default


def is_feature_enabled(key: str) -> bool:
    """Check if a boolean feature flag is enabled."""
    val = get_feature_value(key, False)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "enabled", "on", "yes")
    return bool(val)


def check_gate_cached_or_blocking(key: str) -> bool:
    """Check a gate — blocking means we wait for truthy value.

    For local/dev environments, gates are always open unless
    explicitly overridden to false.
    """
    val = get_feature_value(key, True)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() not in ("false", "0", "disabled", "off", "no")
    return bool(val)


def set_cached_feature_value(key: str, value: Any) -> None:
    """Store a feature value in the cache."""
    _feature_cache[key] = (value, time.time())


def get_all_features() -> dict[str, Any]:
    """Get all known feature flags."""
    result = dict(_feature_overrides)
    for key, (val, _ts) in _feature_cache.items():
        if key not in result:
            result[key] = val
    return result


# Aliases for backward compatibility with modules expecting original TS export names
check_feature_gate = check_gate_cached_or_blocking
check_statsig_feature_gate_cached_may_be_stale = get_feature_value_cached_may_be_stale
get_feature_value_CACHED_WITH_REFRESH = get_feature_value
check_gate_CACHED_OR_BLOCKING = check_gate_cached_or_blocking


def _parse_env_value(val: str) -> Any:
    """Parse an environment variable value into appropriate type."""
    if val.lower() in ("true", "1", "yes", "on"):
        return True
    if val.lower() in ("false", "0", "no", "off"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    if val.startswith("{") or val.startswith("["):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return val
