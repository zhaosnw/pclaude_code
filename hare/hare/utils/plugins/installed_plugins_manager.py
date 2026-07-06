"""
installed_plugins.json read/write, migrations, and CRUD operations.

Port of: src/utils/plugins/installedPluginsManager.ts
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from hare.utils.env_utils import get_hare_config_home_dir

logger = logging.getLogger(__name__)


@dataclass
class InstalledPlugin:
    name: str
    version: str
    source: str = ""  # github, npm, local, marketplace
    install_path: str = ""
    enabled: bool = True
    installed_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _installed_path() -> Path:
    return Path(get_hare_config_home_dir()) / "installed_plugins.json"


def _backup_path() -> Path:
    return Path(get_hare_config_home_dir()) / "installed_plugins.json.bak"


# ---------------------------------------------------------------------------
# Migration: v1 -> v2
# ---------------------------------------------------------------------------


def _is_v1_format(data: dict[str, Any]) -> bool:
    return "plugins" in data and "version" not in data


def _parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    return time.time()


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Convert v1 shape (scalars or partial dicts per plugin name) to v2."""
    raw_plugins = data.get("plugins", {})
    migrated: dict[str, Any] = {}
    now = time.time()
    for name, entry in raw_plugins.items():
        if isinstance(entry, str):
            migrated[name] = {
                "name": name, "version": entry, "source": "",
                "install_path": "", "enabled": True,
                "installed_at": now, "updated_at": now, "metadata": {},
            }
        elif isinstance(entry, dict):
            migrated[name] = {
                "name": name,
                "version": entry.get("version", entry.get("installedVersion", "unknown")),
                "source": entry.get("source", ""),
                "install_path": entry.get("installPath", entry.get("install_path", "")),
                "enabled": entry.get("enabled", True),
                "installed_at": _parse_timestamp(
                    entry.get("installedAt", entry.get("installed_at"))
                ),
                "updated_at": _parse_timestamp(
                    entry.get("lastUpdated", entry.get("updated_at"))
                ),
                "metadata": entry.get("metadata", {}),
            }
        else:
            migrated[name] = {
                "name": name, "version": str(entry), "source": "",
                "install_path": "", "enabled": True,
                "installed_at": now, "updated_at": now, "metadata": {},
            }
    return {"version": 2, "plugins": migrated}


# ---------------------------------------------------------------------------
# Load / save (with auto-migration and atomic writes)
# ---------------------------------------------------------------------------


def load_installed_plugins_v2() -> dict[str, Any]:
    """Return v2 structure or empty default, auto-migrating from v1."""
    p = _installed_path()
    if not p.is_file():
        return {"version": 2, "plugins": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt installed_plugins.json — returning empty v2 structure")
        return {"version": 2, "plugins": {}}

    if not isinstance(data, dict):
        return {"version": 2, "plugins": {}}

    if _is_v1_format(data):
        logger.info("Migrating installed_plugins.json from v1 to v2")
        migrated = _migrate_v1_to_v2(data)
        save_installed_plugins_v2(migrated, create_backup=True)
        return migrated

    # Belt-and-suspenders repair: ensure every entry has a name key
    plugins = data.get("plugins", {})
    for key, entry in list(plugins.items()):
        if isinstance(entry, dict) and "name" not in entry:
            entry["name"] = key
    data["plugins"] = plugins
    data.setdefault("version", 2)
    return data


def load_installed_plugins_from_disk() -> dict[str, Any]:
    return load_installed_plugins_v2()


def save_installed_plugins_v2(data: dict[str, Any], create_backup: bool = False) -> None:
    """Persist installed plugins data atomically via temp file."""
    p = _installed_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    if create_backup and p.is_file():
        try:
            _backup_path().write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass

    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Dependency helpers (used by CRUD operations)
# ---------------------------------------------------------------------------


def _dep_bare_name(dep_spec: str) -> str:
    """Extract the bare plugin name from 'foo@marketplace'."""
    return dep_spec.split("@", 1)[0]


def get_plugin_dependencies(name: str) -> list[str]:
    """Return the declared dependency list of an installed plugin."""
    entry = get_installed_plugin(name)
    if entry is None:
        return []
    return entry.get("dependencies", []) or []


def find_installed_dependents(name: str) -> list[str]:
    """Return names of all installed plugins that declare a dependency on *name*."""
    plugins = load_installed_plugins_v2().get("plugins", {})
    result: list[str] = []
    target = name.lower()
    for pname, entry in plugins.items():
        if pname.lower() == target:
            continue
        deps = entry.get("dependencies", []) or []
        if any(_dep_bare_name(d).lower() == target for d in deps):
            result.append(pname)
    return sorted(result)


def find_enabled_dependents(name: str) -> list[str]:
    """Return names of *enabled* plugins that declare a dependency on *name*."""
    plugins = load_installed_plugins_v2().get("plugins", {})
    result: list[str] = []
    target = name.lower()
    for pname, entry in plugins.items():
        if pname.lower() == target or not entry.get("enabled", True):
            continue
        deps = entry.get("dependencies", []) or []
        if any(_dep_bare_name(d).lower() == target for d in deps):
            result.append(pname)
    return sorted(result)


def find_missing_dependencies(name: str) -> list[str]:
    """Return dependencies of *name* that are not installed at all."""
    deps = get_plugin_dependencies(name)
    plugins = load_installed_plugins_v2().get("plugins", {})
    installed_names = {k.lower() for k in plugins}
    return [d for d in deps if _dep_bare_name(d).lower() not in installed_names]


def find_disabled_dependencies(name: str) -> list[str]:
    """Return dependencies of *name* that are installed but disabled."""
    deps = get_plugin_dependencies(name)
    plugins = load_installed_plugins_v2().get("plugins", {})
    disabled: list[str] = []
    for dep in deps:
        dep_name = _dep_bare_name(dep)
        entry = next(
            (e for k, e in plugins.items() if k.lower() == dep_name.lower()), None
        )
        if entry and not entry.get("enabled", True):
            disabled.append(dep)
    return disabled


# ---------------------------------------------------------------------------
# Core CRUD (with dependency-aware guards)
# ---------------------------------------------------------------------------


def get_installed_plugin(name: str) -> Optional[dict[str, Any]]:
    """Get a specific installed plugin by name."""
    data = load_installed_plugins_v2()
    return data.get("plugins", {}).get(name)


def install_plugin(name: str, version: str, source: str = "", install_path: str = "",
                   metadata: Optional[dict[str, Any]] = None,
                   dependencies: Optional[list[str]] = None) -> bool:
    """Register a plugin as installed, optionally recording its dependency list."""
    data = load_installed_plugins_v2()
    plugins = data.get("plugins", {})

    if name in plugins:
        existing = plugins[name]
        existing["version"] = version
        existing["updated_at"] = time.time()
        if source:
            existing["source"] = source
        if install_path:
            existing["install_path"] = install_path
        if metadata:
            existing.setdefault("metadata", {}).update(metadata)
        if dependencies is not None:
            existing["dependencies"] = dependencies
    else:
        entry: dict[str, Any] = {
            "name": name, "version": version, "source": source,
            "install_path": install_path, "enabled": True,
            "installed_at": time.time(), "updated_at": time.time(),
            "metadata": metadata or {},
        }
        if dependencies:
            entry["dependencies"] = dependencies
        plugins[name] = entry

    data["plugins"] = plugins
    save_installed_plugins_v2(data)
    return True


def uninstall_plugin(name: str, force: bool = False) -> bool:
    """Remove a plugin from the installed list.

    When *force* is False, refuses if other installed plugins depend on
    *name* (reverse-dependency guard).
    """
    if not force:
        rdeps = find_installed_dependents(name)
        if rdeps:
            logger.warning(
                "Cannot uninstall %s — required by: %s", name, ", ".join(rdeps)
            )
            return False

    data = load_installed_plugins_v2()
    plugins = data.get("plugins", {})
    if name not in plugins:
        return False
    del plugins[name]
    data["plugins"] = plugins
    save_installed_plugins_v2(data)
    return True


def enable_plugin(name: str) -> bool:
    """Enable a plugin."""
    data = load_installed_plugins_v2()
    plugins = data.get("plugins", {})
    if name not in plugins:
        return False
    plugins[name]["enabled"] = True
    plugins[name]["updated_at"] = time.time()
    save_installed_plugins_v2(data)
    return True


def disable_plugin(name: str, force: bool = False) -> bool:
    """Disable a plugin.

    When *force* is False, refuses if any *enabled* installed plugin depends
    on *name* (reverse-dependency guard).
    """
    if not force:
        rdeps = find_enabled_dependents(name)
        if rdeps:
            logger.warning(
                "Cannot disable %s — required by enabled plugins: %s",
                name, ", ".join(rdeps)
            )
            return False

    data = load_installed_plugins_v2()
    plugins = data.get("plugins", {})
    if name not in plugins:
        return False
    plugins[name]["enabled"] = False
    plugins[name]["updated_at"] = time.time()
    save_installed_plugins_v2(data)
    return True


def is_plugin_enabled(name: str) -> bool:
    """Check if a plugin is enabled."""
    plugin = get_installed_plugin(name)
    if plugin is None:
        return False
    return plugin.get("enabled", True)


def list_installed_plugins() -> list[dict[str, Any]]:
    """List all installed plugins."""
    data = load_installed_plugins_v2()
    plugins = data.get("plugins", {})
    return sorted(plugins.values(), key=lambda p: p.get("name", ""))


def get_plugin_count() -> dict[str, int]:
    """Get plugin counts by status."""
    plugins = load_installed_plugins_v2().get("plugins", {})
    total = len(plugins)
    enabled = sum(1 for p in plugins.values() if p.get("enabled", True))
    return {"total": total, "enabled": enabled, "disabled": total - enabled}
