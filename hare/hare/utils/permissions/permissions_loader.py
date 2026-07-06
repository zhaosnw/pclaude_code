"""Load permissions from settings files. Port of permissionsLoader.ts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_HARE_HOME = Path.home() / ".hare"


def _get_settings_paths() -> dict[str, Path | None]:
    """Return settings file paths per source."""
    return {
        "userSettings": _HARE_HOME / "settings.json",
        "projectSettings": Path(os.getcwd()) / ".hare" / "settings.json",
        "localSettings": Path(os.getcwd()) / ".hare" / "settings.local.json",
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_permission_rules_from_settings() -> dict[str, dict[str, list[str]]]:
    """Load all permission rules from user, project, and local settings.

    Returns {source: {allow: [...], deny: [...], ask: [...]}}.
    """
    result: dict[str, dict[str, list[str]]] = {}
    for source, path in _get_settings_paths().items():
        if path and path.exists():
            rules = get_permission_rules_for_source(source, path)
            if any(rules.values()):
                result[source] = rules
    return result


def get_permission_rules_for_source(
    source: str,
    path: Path,
) -> dict[str, list[str]]:
    """Read permission rules from a single settings file.

    Returns {allow: [...], deny: [...], ask: [...]}.
    """
    data = _load_json(path)
    permissions = data.get("permissions", {})

    if isinstance(permissions, dict):
        return {
            "allow": _ensure_list(permissions.get("allow")),
            "deny": _ensure_list(permissions.get("deny")),
            "ask": _ensure_list(permissions.get("ask")),
        }

    # Legacy flat list — treat as deny rules
    if isinstance(permissions, list):
        return {"allow": [], "deny": [str(r) for r in permissions], "ask": []}

    return {"allow": [], "deny": [], "ask": []}


def _ensure_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def add_permission_rule_to_settings(
    behavior: str,
    rule_content: str,
    source: str = "userSettings",
) -> bool:
    """Add a rule string to the specified settings source file."""
    paths = _get_settings_paths()
    path = paths.get(source)
    if not path:
        return False

    data = _load_json(path)
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {"allow": [], "deny": [], "ask": []}

    data.setdefault("permissions", permissions)
    key = {"allow": "allow", "deny": "deny", "ask": "ask"}.get(behavior, "deny")
    rules = permissions.setdefault(key, [])
    if rule_content not in rules:
        rules.append(rule_content)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def delete_permission_rule_from_settings(
    rule_content: str,
    source: str | None = None,
) -> bool:
    """Delete a rule string from settings files.

    If source is None, searches all settings sources.
    """
    if source:
        paths = {source: _get_settings_paths().get(source)}
    else:
        paths = _get_settings_paths()

    removed = False
    for src, path in paths.items():
        if not path or not path.is_file():
            continue
        data = _load_json(path)
        permissions = data.get("permissions", {})
        if not isinstance(permissions, dict):
            continue
        for key in ("allow", "deny", "ask"):
            rules = permissions.get(key, [])
            if rule_content in rules:
                rules.remove(rule_content)
                removed = True
        if removed:
            try:
                path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass

    return removed


def should_allow_managed_permission_rules_only() -> bool:
    """Check if policy restricts to managed rules only."""
    policy_path = _HARE_HOME / "policySettings.json"
    data = _load_json(policy_path)
    if isinstance(data, dict):
        return bool(data.get("allowManagedPermissionRulesOnly", False))
    return False


def load_permissions_from_path(path: Path) -> list[dict[str, Any]]:
    """Legacy: load raw permissions list from a file."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        perms = data.get("permissions")
        return perms if isinstance(perms, list) else []
    except (json.JSONDecodeError, OSError):
        return []
