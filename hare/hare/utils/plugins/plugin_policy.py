"""
Plugin policy checks (managed / policySettings).

Port of: src/utils/plugins/pluginPolicy.ts
"""

from __future__ import annotations

from typing import Any, cast

from hare.utils.settings.settings import get_settings_for_source


def is_plugin_blocked_by_policy(plugin_id: str) -> bool:
    policy_settings = get_settings_for_source("policySettings")
    if not policy_settings:
        return False
    enabled = cast(dict[str, Any], policy_settings.get("enabledPlugins") or {})
    return enabled.get(plugin_id) is False
