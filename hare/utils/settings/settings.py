"""
Settings management — load, merge, and cache settings from 6 config sources.

Port of: src/utils/settings/settings.ts

Six-layer priority chain (lowest to highest):
  pluginSettings → userSettings → projectSettings → localSettings → flagSettings → policySettings

Merge rules (matching TS settingsMergeCustomizer):
  - Arrays: concatenate + deduplicate (uniq), NOT replace
  - Objects: deep merge (shallow update for now)
  - Scalars: later source overwrites earlier

Security boundary (matching TS 5.2):
  - projectSettings is excluded from security-sensitive checks
  - policySettings uses "first non-empty source wins" for enterprise policy
"""

# mypy: disable-error-code="literal-required"
# TypedDict key access via string variables is unavoidable in a settings merge
# engine that iterates over dynamic source dictionaries.

from __future__ import annotations

import json
import os
from typing import Any, Optional

from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.settings.types import SettingsJson
from hare.utils.settings.constants import SettingSource, SETTING_SOURCES

# ---------------------------------------------------------------------------
# Settings cache (matching TS settingsCache.ts)
# ---------------------------------------------------------------------------

# Per-source parse cache (by file path)
_parse_file_cache: dict[str, Optional[dict[str, Any]]] = {}

# Plugin settings base layer (lowest priority, matching TS pluginSettingsBase)
_plugin_settings_base: SettingsJson | None = None

# Session-level merged settings cache
_session_settings_cache: SettingsJson | None = None
_session_project_dir: str = ""


def reset_settings_cache() -> None:
    """Clear all settings caches. TS: clearSettingsCaches."""
    _parse_file_cache.clear()
    global _session_settings_cache, _session_project_dir
    _session_settings_cache = None
    _session_project_dir = ""


def get_plugin_settings_base() -> SettingsJson | None:
    """Get the plugin settings base layer. TS: getPluginSettingsBase."""
    return _plugin_settings_base


def set_plugin_settings_base(settings: SettingsJson) -> None:
    """Set the plugin settings base layer. TS: setPluginSettingsBase."""
    global _plugin_settings_base
    _plugin_settings_base = settings


def clear_plugin_settings_base() -> None:
    """Clear the plugin settings base layer. TS: clearPluginSettingsBase."""
    global _plugin_settings_base
    _plugin_settings_base = None


# ---------------------------------------------------------------------------
# Main settings loading
# ---------------------------------------------------------------------------


def get_initial_settings(project_dir: str = "") -> SettingsJson:
    """Merged settings snapshot from all 6 sources.

    TS: loadSettingsFromDisk() — starts with pluginSettingsBase,
    then iterates SETTING_SOURCES in priority order, merging each on top.
    """
    global _session_settings_cache, _session_project_dir

    # Return cached session-level result if project dir hasn't changed
    if project_dir == _session_project_dir and _session_settings_cache is not None:
        return _session_settings_cache

    merged: SettingsJson = {}

    # Layer 0: pluginSettings (lowest priority, matching TS settings.ts L661-668)
    plugin_base = get_plugin_settings_base()
    if plugin_base:
        _merge_settings(merged, plugin_base)

    # Layers 1-6: canonical sources in priority order
    for source in SETTING_SOURCES:
        source_settings = get_settings_for_source(source, project_dir=project_dir)
        if source_settings:
            _merge_settings(merged, source_settings)

    _session_settings_cache = merged
    _session_project_dir = project_dir
    return merged


get_settings = get_initial_settings
get_settings_deprecated = get_initial_settings


# ---------------------------------------------------------------------------
# Source-specific loading
# ---------------------------------------------------------------------------


def get_settings_for_source(
    source: SettingSource,
    project_dir: str = "",
) -> Optional[SettingsJson]:
    """Get settings for a specific source (with per-file caching)."""
    path = get_settings_file_path_for_source(source, project_dir=project_dir)
    if not path:
        return None

    # Check per-file parse cache
    if path in _parse_file_cache:
        cached = _parse_file_cache[path]
        if cached is not None:
            return cached.get("settings")

    result = parse_settings_file(path)
    _parse_file_cache[path] = result
    return result.get("settings") if result else None


def parse_settings_file(path: str) -> dict[str, Any]:
    """Parse a settings JSON file. Returns {"settings": ..., "errors": [...]}."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return {"settings": {}, "errors": []}
        data = json.loads(content)
        if not isinstance(data, dict):
            return {
                "settings": None,
                "errors": [{"message": "Settings must be a JSON object"}],
            }
        return {"settings": data, "errors": []}
    except FileNotFoundError:
        return {"settings": None, "errors": []}
    except json.JSONDecodeError as e:
        return {"settings": None, "errors": [{"message": f"Invalid JSON: {e}"}]}


def get_settings_file_path_for_source(
    source: SettingSource,
    project_dir: str = "",
) -> Optional[str]:
    """Get the file path for a settings source."""
    if source == "userSettings":
        return os.path.join(get_hare_config_home_dir(), "settings.json")
    elif source == "projectSettings":
        if not project_dir:
            return None
        return os.path.join(project_dir, ".hare", "settings.json")
    elif source == "localSettings":
        if not project_dir:
            return None
        return os.path.join(project_dir, ".hare", "settings.local.json")
    elif source == "policySettings":
        return _resolve_policy_settings_path()
    elif source == "flagSettings":
        return None  # Provided via CLI flag at runtime
    return None


def reload_settings(project_dir: str = "") -> SettingsJson:
    """Explicitly clear settings cache and reload merged settings from disk."""
    reset_settings_cache()
    return get_initial_settings(project_dir=project_dir)


# ---------------------------------------------------------------------------
# Settings merge (matching TS settingsMergeCustomizer)
# ---------------------------------------------------------------------------


def _uniq_preserve_order(items: list[Any]) -> list[Any]:
    """Deduplicate a list while preserving order. TS: uniq()."""
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        key = (
            json.dumps(item, sort_keys=True)
            if isinstance(item, (dict, list))
            else str(item)
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _merge_settings(target: SettingsJson, source: SettingsJson) -> None:
    """Merge source settings into target (source wins for conflicts).

    TS settingsMergeCustomizer rules:
    - Arrays: concatenate and deduplicate (NOT replace)
    - Objects: shallow update (TS uses lodash mergeWith for deep merge;
      hare uses dict.update for one-level deep)
    - Scalars: later source overwrites earlier
    """
    for key, value in source.items():
        if key not in target:
            target[key] = value
        elif isinstance(value, dict) and isinstance(target[key], dict):
            # Deep merge for objects (one level — TS uses lodash mergeWith)
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, list) and isinstance(
                    target[key].get(sub_key), list
                ):
                    # Array sub-fields: concat + dedup
                    combined = target[key][sub_key] + sub_value
                    target[key][sub_key] = _uniq_preserve_order(combined)
                elif isinstance(sub_value, dict) and isinstance(
                    target[key].get(sub_key), dict
                ):
                    target[key][sub_key].update(sub_value)
                else:
                    target[key][sub_key] = sub_value
        elif isinstance(value, list) and isinstance(target[key], list):
            # Arrays: concatenate + deduplicate (TS settingsMergeCustomizer)
            combined = target[key] + value
            target[key] = _uniq_preserve_order(combined)
        else:
            # Scalar: overwrite
            target[key] = value


# ---------------------------------------------------------------------------
# Update settings for a source
# ---------------------------------------------------------------------------


def update_settings_for_source(
    source: SettingSource,
    updates: dict[str, Any],
    project_dir: str = "",
) -> None:
    """Update settings for a specific source. TS: updateSettingsForSource."""
    path = get_settings_file_path_for_source(source, project_dir=project_dir)
    if not path:
        return
    current = parse_settings_file(path).get("settings") or {}
    current.update(updates)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    # Invalidate cache for this path
    _parse_file_cache.pop(path, None)
    global _session_settings_cache
    _session_settings_cache = None


# ---------------------------------------------------------------------------
# Policy settings resolution ("first non-empty source wins")
# ---------------------------------------------------------------------------


def _resolve_policy_settings_path() -> Optional[str]:
    """Resolve policy settings path using 'first non-empty source wins' strategy.

    TS: getSettingsForSourceUncached for policySettings:
    1. Remote managed settings (API cache)
    2. MDM (macOS plist / Windows HKLM registry)
    3. managed-settings.json + managed-settings.d/*.json
    4. HKCU registry (Windows user-level)

    First non-empty source wins — no merging across sub-sources.
    """
    # 1. Remote managed settings placeholder (TS: getRemoteManagedSettingsSyncFromCache)
    # In a full implementation, this would check an in-memory cache populated by
    # a background sync job. For now, check env var override.
    remote_path = os.environ.get("HARE_REMOTE_SETTINGS_PATH")
    if remote_path and os.path.exists(remote_path):
        return remote_path

    # 2. MDM (macOS plist / Windows registry) placeholder
    mdm_path = _resolve_mdm_settings_path()
    if mdm_path and os.path.exists(mdm_path):
        return mdm_path

    # 3. managed-settings.json + drop-ins
    managed_base = _get_managed_settings_base_path()
    if managed_base and os.path.exists(managed_base):
        return managed_base

    return managed_base  # fallback path even if file doesn't exist yet


def _resolve_mdm_settings_path() -> Optional[str]:
    """Resolve MDM settings path. TS: reads plist (macOS) or HKLM (Windows)."""
    import platform

    system = platform.system()
    if system == "Darwin":
        mdm_path = os.environ.get("HARE_MDM_SETTINGS_PATH")
        if mdm_path:
            return mdm_path
        # Default MDM plist path
        candidate = "/Library/Managed Preferences/com.anthropic.hare-code.plist"
        if os.path.exists(candidate):
            return candidate
    elif system == "Windows":
        mdm_path = os.environ.get("HARE_MDM_SETTINGS_PATH")
        if mdm_path:
            return mdm_path
    return None


def _get_managed_settings_base_path() -> str:
    """Get the managed settings base file path based on platform."""
    import sys

    if sys.platform == "darwin":
        return "/Library/Application Support/HareCode/managed-settings.json"
    elif sys.platform == "win32":
        return os.path.join(
            os.environ.get("PROGRAMDATA", "C:\\ProgramData"),
            "HareCode",
            "managed-settings.json",
        )
    else:
        return "/etc/hare-code/managed-settings.json"


# ---------------------------------------------------------------------------
# Security boundary functions (matching TS 5.2 — projectSettings exclusion)
# ---------------------------------------------------------------------------


def _read_setting_excluding_project(
    setting_key: str,
    sources: list[str],
) -> Any | None:
    """Read a setting from specified sources, intentionally excluding projectSettings.

    TS: projectSettings is intentionally excluded from security-sensitive checks
    because a malicious .claude/settings.json could otherwise auto-bypass dialogs
    (RCE risk via auto-accepting dangerous mode, etc.).
    """
    for source in sources:
        path = get_settings_file_path_for_source(source)  # type: ignore[arg-type]
        if not path:
            continue
        result = parse_settings_file(path).get("settings")
        if result and setting_key in result:
            return result[setting_key]
    return None


# Sources that are trusted for security-sensitive checks (excludes projectSettings)
TRUSTED_SOURCES_EXCLUDING_PROJECT = [
    "policySettings",
    "flagSettings",
    "localSettings",
    "userSettings",
]


def has_skip_dangerous_mode_permission_prompt() -> bool:
    """Check if user has opted to skip dangerous mode permission prompt.

    TS: hasSkipDangerousModePermissionPrompt() — reads from user/local/flag/policy
    settings only; projectSettings intentionally excluded (RCE risk).
    """
    val = _read_setting_excluding_project(
        "skipDangerousModePermissionPrompt",
        TRUSTED_SOURCES_EXCLUDING_PROJECT,
    )
    return bool(val)


def has_auto_mode_opt_in() -> bool:
    """Check if user has opted into auto (classifier) mode.

    TS: hasAutoModeOptIn() — excludes projectSettings.
    """
    val = _read_setting_excluding_project(
        "autoModeOptIn",
        TRUSTED_SOURCES_EXCLUDING_PROJECT,
    )
    return bool(val)


def get_use_auto_mode_during_plan() -> bool:
    """Check if auto mode should be used during plan mode.

    TS: getUseAutoModeDuringPlan() — excludes projectSettings.
    """
    val = _read_setting_excluding_project(
        "useAutoModeDuringPlan",
        TRUSTED_SOURCES_EXCLUDING_PROJECT,
    )
    return bool(val)


def get_auto_mode_config() -> dict[str, Any] | None:
    """Get auto mode configuration. TS: getAutoModeConfig() — excludes projectSettings."""
    val = _read_setting_excluding_project(
        "autoModeConfig",
        TRUSTED_SOURCES_EXCLUDING_PROJECT,
    )
    if isinstance(val, dict):
        return val
    return None


def get_managed_hooks_only() -> bool:
    """Check if only managed hooks should be allowed.

    TS: reads allowManagedHooksOnly from policySettings.
    """
    policy_path = _resolve_policy_settings_path()
    if not policy_path:
        return False
    result = parse_settings_file(policy_path).get("settings")
    if result and isinstance(result, dict):
        return bool(result.get("allowManagedHooksOnly", False))
    return False


def get_managed_permission_rules_only() -> bool:
    """Check if only managed permission rules should be allowed."""
    policy_path = _resolve_policy_settings_path()
    if not policy_path:
        return False
    result = parse_settings_file(policy_path).get("settings")
    if result and isinstance(result, dict):
        return bool(result.get("allowManagedPermissionRulesOnly", False))
    return False


def get_strict_plugin_only_customization() -> bool | list[str]:
    """Get strict plugin-only customization policy.

    TS: reads strictPluginOnlyCustomization from policySettings.
    Can be True (lock all surfaces) or [surface_name, ...] (lock specific).
    """
    policy_path = _resolve_policy_settings_path()
    if not policy_path:
        return False
    result = parse_settings_file(policy_path).get("settings")
    if result and isinstance(result, dict):
        val = result.get("strictPluginOnlyCustomization", False)
        if isinstance(val, list):
            return val
        return bool(val)
    return False
