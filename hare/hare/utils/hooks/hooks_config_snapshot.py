"""
Hooks configuration snapshot for startup vs. runtime refresh.

Port of: src/utils/hooks/hooksConfigSnapshot.ts
"""

from __future__ import annotations

from typing import Any

_initial_hooks_config: dict[str, Any] | None = None


def _get_settings_for_source(_source: str) -> dict[str, Any] | None:
    """Stub: wire to settings.settings.get_settings_for_source."""
    return None


def _get_settings_deprecated() -> dict[str, Any]:
    return {}


def _is_restricted_to_plugin_only(_key: str) -> bool:
    return False


def _reset_settings_cache() -> None:
    pass


def _reset_sdk_init_state() -> None:
    pass


def get_hooks_from_allowed_sources() -> dict[str, Any]:
    policy = _get_settings_for_source("policySettings") or {}
    if policy.get("disableAllHooks") is True:
        return {}
    if policy.get("allowManagedHooksOnly") is True:
        return policy.get("hooks") or {}
    if _is_restricted_to_plugin_only("hooks"):
        return policy.get("hooks") or {}
    merged = _get_settings_deprecated()
    if merged.get("disableAllHooks") is True:
        return policy.get("hooks") or {}
    return merged.get("hooks") or {}


def should_allow_managed_hooks_only() -> bool:
    policy = _get_settings_for_source("policySettings") or {}
    if policy.get("allowManagedHooksOnly") is True:
        return True
    if (
        _get_settings_deprecated().get("disableAllHooks") is True
        and policy.get("disableAllHooks") is not True
    ):
        return True
    return False


def should_disable_all_hooks_including_managed() -> bool:
    policy = _get_settings_for_source("policySettings") or {}
    return policy.get("disableAllHooks") is True


def capture_hooks_config_snapshot() -> None:
    global _initial_hooks_config
    _initial_hooks_config = get_hooks_from_allowed_sources()


def update_hooks_config_snapshot() -> None:
    global _initial_hooks_config
    _reset_settings_cache()
    _initial_hooks_config = get_hooks_from_allowed_sources()


def get_hooks_config_from_snapshot() -> dict[str, Any] | None:
    if _initial_hooks_config is None:
        capture_hooks_config_snapshot()
    return _initial_hooks_config


def reset_hooks_config_snapshot() -> None:
    global _initial_hooks_config
    _initial_hooks_config = None
    _reset_sdk_init_state()
