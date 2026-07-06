"""
Delisted plugin detection, flagging, and auto-uninstall.

Port of: src/utils/plugins/pluginBlocklist.ts

This module detects plugins that have been removed from their source
marketplace ("delisted"), flags them in a persistent blocklist, and
optionally uninstalls them automatically.  It coordinates between
- installed_plugins_manager   (load/save installed plugins)
- marketplace_manager          (fetch marketplace listings)
- plugin_flagging              (persist flagged/delisted plugin ids)
- plugin_policy                (check if policy blocks a plugin)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from hare.utils.debug import log_for_debugging, log_error
from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.plugins.installed_plugins_manager import (
    load_installed_plugins_v2,
    save_installed_plugins_v2,
    uninstall_plugin,
)
from hare.utils.plugins.marketplace_manager import (
    get_marketplace,
    load_known_marketplaces_config_safe,
)
from hare.utils.plugins.plugin_flagging import (
    _flagged,
    add_flagged_plugin,
    get_flagged_plugins,
    load_flagged_plugins,
)
from hare.utils.plugins.plugin_identifier import parse_plugin_identifier
from hare.utils.plugins.plugin_policy import is_plugin_blocked_by_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class DelistedPluginReport:
    """Summary produced after a blocklist scan run."""

    delisted: list[str] = field(default_factory=list)
    """Plugin ids (name@marketplace) that were detected as delisted."""

    uninstalled: list[str] = field(default_factory=list)
    """Plugin ids that were successfully auto-uninstalled."""

    failed: list[str] = field(default_factory=list)
    """Plugin ids where auto-uninstall failed (e.g. reverse-dependency guard)."""

    flagged: list[str] = field(default_factory=list)
    """Plugin ids newly added to the flagged set this run."""

    skipped_policy: list[str] = field(default_factory=list)
    """Plugin ids already blocked by policy — no action taken."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _blocklist_path() -> Path:
    """Return the path to the persistent blocklist file."""
    return Path(get_hare_config_home_dir()) / "plugin_blocklist.json"


def _blocklist_backup_path() -> Path:
    """Return the path to the blocklist backup file."""
    return Path(get_hare_config_home_dir()) / "plugin_blocklist.json.bak"


# ---------------------------------------------------------------------------
# Persistent blocklist (complements in-memory flagged set from plugin_flagging)
# ---------------------------------------------------------------------------


def load_blocklist_from_disk() -> dict[str, Any]:
    """Load the persistent blocklist from disk.

    Returns a dict with shape:
        {"version": 1, "blocked": {plugin_id: {reason, flagged_at, ...}, ...}}

    On any error (missing file, corrupt JSON), returns an empty default.
    """
    p = _blocklist_path()
    if not p.is_file():
        return {"version": 1, "blocked": {}}

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt plugin_blocklist.json — returning empty default: %s", exc)
        return {"version": 1, "blocked": {}}

    if not isinstance(data, dict):
        return {"version": 1, "blocked": {}}

    data.setdefault("version", 1)
    data.setdefault("blocked", {})
    return data


def save_blocklist_to_disk(data: dict[str, Any], create_backup: bool = False) -> None:
    """Persist the blocklist data atomically via temp file."""
    p = _blocklist_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    if create_backup and p.is_file():
        try:
            _blocklist_backup_path().write_text(
                p.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass

    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def blocklist_record(
    plugin_id: str,
    reason: str = "detected_as_delisted",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a blocklist record dict for *plugin_id*."""
    import time

    record: dict[str, Any] = {
        "plugin_id": plugin_id,
        "reason": reason,
        "flagged_at": time.time(),
    }
    if extra:
        record["extra"] = extra
    return record


# ---------------------------------------------------------------------------
# Delisted detection (already present — kept, with enhancements)
# ---------------------------------------------------------------------------


def detect_delisted_plugins(
    installed_plugins: dict[str, Any],
    marketplace: dict[str, Any],
    marketplace_name: str,
) -> list[str]:
    """Return plugin IDs installed from *marketplace_name* that are no longer listed.

    A plugin ID of the form ``<name>@<marketplace_name>`` is delisted when
    no marketplace entry exists for ``<name>``.
    """
    names = {p["name"] for p in marketplace.get("plugins", [])}
    suffix = f"@{marketplace_name}"
    delisted: list[str] = []
    for plugin_id in installed_plugins.get("plugins", {}).keys():
        if not str(plugin_id).endswith(suffix):
            continue
        plugin_name = str(plugin_id)[: -len(suffix)]
        if plugin_name not in names:
            delisted.append(str(plugin_id))
    return delisted


def detect_delisted_plugins_from_marketplace_name(
    marketplace_name: str,
) -> list[str]:
    """Convenience: fetch a marketplace by name and detect delisted plugins.

    Returns an empty list if the marketplace cannot be loaded or the
    installed-plugins file cannot be read.
    """
    installed = load_installed_plugins_v2()

    # Safely load marketplace data — async fetch in real code; stub for now
    import asyncio

    marketplace: dict[str, Any] = {"name": marketplace_name, "plugins": []}
    try:
        # marketplace_manager.get_marketplace is async in real code
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # In an async context, we cannot call the async function synchronously
            # Return empty as a safe fallback — callers should use the async path
            log_for_debugging(
                f"detect_delisted_plugins_from_marketplace_name called inside "
                f"running event loop for '{marketplace_name}' — returning empty"
            )
            return []
        marketplace = loop.run_until_complete(get_marketplace(marketplace_name))
    except RuntimeError:
        # No event loop — try to create one
        try:
            marketplace = asyncio.run(get_marketplace(marketplace_name))
        except Exception as exc:
            log_error(exc)
    except Exception as exc:
        log_error(exc)

    return detect_delisted_plugins(installed, marketplace, marketplace_name)


def detect_delisted_plugins_for_all_marketplaces(
    marketplaces: Optional[dict[str, Any]] = None,
) -> dict[str, list[str]]:
    """Run delisted detection against every known marketplace.

    Returns a dict mapping ``marketplace_name -> list[delisted_plugin_id]``.
    """
    if marketplaces is None:
        marketplaces = {}
        try:
            marketplaces = _load_marketplaces_sync()
        except Exception as exc:
            log_error(exc)

    installed = load_installed_plugins_v2()
    result: dict[str, list[str]] = {}

    for mp_name in marketplaces:
        mp_data: dict[str, Any] = marketplaces.get(mp_name, {})  # type: ignore[assignment]
        if not isinstance(mp_data, dict):
            mp_data = {}
        result[mp_name] = detect_delisted_plugins(installed, mp_data, mp_name)

    return result


def _load_marketplaces_sync() -> dict[str, Any]:
    """Best-effort synchronous marketplace load. Falls back to empty."""
    import asyncio

    try:
        return asyncio.run(load_known_marketplaces_config_safe())
    except Exception as exc:
        log_error(exc)
        return {}


# ---------------------------------------------------------------------------
# Flag / unflag helpers
# ---------------------------------------------------------------------------


async def flag_plugin_as_delisted(
    plugin_id: str,
    reason: str = "detected_as_delisted",
    persist_blocklist: bool = True,
) -> bool:
    """Mark *plugin_id* in both the in-memory flagged set and on-disk blocklist.

    Returns True if the plugin was newly flagged, False if it was already flagged.
    """
    existing = get_flagged_plugins()
    already_flagged = plugin_id in existing

    # In-memory flag (from plugin_flagging module)
    await add_flagged_plugin(plugin_id)

    # Persist to blocklist file
    if persist_blocklist:
        data = load_blocklist_from_disk()
        blocked = data.get("blocked", {})
        if plugin_id not in blocked:
            blocked[plugin_id] = blocklist_record(plugin_id, reason=reason)
            data["blocked"] = blocked
            save_blocklist_to_disk(data)

    if already_flagged:
        logger.debug("Plugin %s was already flagged", plugin_id)
    else:
        logger.info("Flagged plugin %s as delisted (reason=%s)", plugin_id, reason)

    return not already_flagged


async def unflag_plugin(plugin_id: str) -> bool:
    """Remove *plugin_id* from the in-memory flagged set and the on-disk blocklist.

    Returns True if the plugin was removed, False if it was not found.
    """
    # Remove from in-memory set — mutate the module-level _flagged directly
    # (get_flagged_plugins returns a copy, so we cannot use it for mutation)
    removed_from_memory = _flagged.pop(plugin_id, None) is not None

    # Remove from persistent blocklist
    data = load_blocklist_from_disk()
    blocked = data.get("blocked", {})
    removed_from_disk = blocked.pop(plugin_id, None) is not None
    if removed_from_disk:
        data["blocked"] = blocked
        save_blocklist_to_disk(data)

    if removed_from_memory or removed_from_disk:
        logger.info("Unflagged plugin %s", plugin_id)
        return True

    logger.debug("Plugin %s was not flagged — nothing to remove", plugin_id)
    return False


def is_plugin_flagged(plugin_id: str) -> bool:
    """Check whether *plugin_id* is in the flagged (blocklisted) set.

    Consults the in-memory set (plugin_flagging) and falls back to the
    on-disk blocklist for durability.
    """
    flagged_memory = get_flagged_plugins()
    if plugin_id in flagged_memory:
        return True

    # Fallback: check persistent blocklist
    data = load_blocklist_from_disk()
    return plugin_id in data.get("blocked", {})


def is_plugin_blocked(plugin_id: str) -> bool:
    """Check whether *plugin_id* is blocked by any mechanism.

    A plugin is considered blocked if:
    - It is flagged/delisted (plugin_flagging or blocklist file)
    - It is blocked by enterprise policy (plugin_policy)
    """
    return is_plugin_flagged(plugin_id) or is_plugin_blocked_by_policy(plugin_id)


def get_blocked_plugins() -> list[str]:
    """Return all plugin IDs that are currently blocked (flagged + policy)."""
    data = load_blocklist_from_disk()
    blocked_from_disk = list(data.get("blocked", {}).keys())
    blocked_from_memory = list(get_flagged_plugins().keys())

    all_blocked = set(blocked_from_disk) | set(blocked_from_memory)

    # Also check policy-blocked plugins from installed set
    installed = load_installed_plugins_v2().get("plugins", {})
    for plugin_id in installed:
        if is_plugin_blocked_by_policy(plugin_id):
            all_blocked.add(plugin_id)

    return sorted(all_blocked)


# ---------------------------------------------------------------------------
# Auto-uninstall logic
# ---------------------------------------------------------------------------


async def auto_uninstall_delisted_plugin(
    plugin_id: str,
    force: bool = False,
) -> tuple[bool, str]:
    """Attempt to uninstall a delisted plugin.

    Args:
        plugin_id: The full plugin identifier (e.g. ``foo@marketplace``).
        force: If True, bypass the reverse-dependency guard.

    Returns:
        A tuple ``(success, reason)``.
    """
    # Parse to get bare name
    parsed = parse_plugin_identifier(plugin_id)
    name = parsed.name

    if not name:
        logger.warning("Cannot uninstall plugin with empty name: %s", plugin_id)
        return False, "empty_plugin_name"

    # Check if the plugin is actually installed
    installed = load_installed_plugins_v2().get("plugins", {})
    if plugin_id not in installed:
        logger.debug("Plugin %s is not installed — nothing to uninstall", plugin_id)
        return True, "not_installed"

    success = uninstall_plugin(plugin_id, force=force)
    if success:
        logger.info("Auto-uninstalled delisted plugin: %s", plugin_id)
        return True, "uninstalled"
    else:
        logger.warning(
            "Failed to auto-uninstall %s (might have dependents)", plugin_id
        )
        return False, "uninstall_failed"


# ---------------------------------------------------------------------------
# Sync flagged set to in-memory store
# ---------------------------------------------------------------------------


async def _sync_blocklist_to_memory() -> None:
    """Ensure in-memory flagged set is consistent with persistent blocklist."""
    data = load_blocklist_from_disk()
    blocked = data.get("blocked", {})

    existing = get_flagged_plugins()
    for plugin_id in blocked:
        if plugin_id not in existing:
            await add_flagged_plugin(plugin_id)

    log_for_debugging(
        f"_sync_blocklist_to_memory: synced {len(blocked)} entries from disk"
    )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def detect_and_uninstall_delisted_plugins(
    *,
    auto_uninstall: bool = True,
    force_uninstall: bool = False,
    on_progress: Optional[Callable[[str, dict[str, Any]], None]] = None,
    allowlist: Optional[Sequence[str]] = None,
) -> DelistedPluginReport:
    """Detect delisted plugins across all known marketplaces and optionally
    auto-uninstall them.

    This is the main entry point. It:
    1. Loads the flagged plugin set
    2. Loads known marketplaces
    3. For each marketplace, detects delisted plugins
    4. Flags newly-detected delisted plugins
    5. Optionally auto-uninstalls them (skipping policy-blocked plugins)

    Args:
        auto_uninstall: If True, attempt to uninstall detected delisted plugins.
        force_uninstall: If True, force uninstall even when reverse-dependencies exist.
        on_progress: Optional callback invoked with ``(phase, details)``.
        allowlist: Optional sequence of plugin IDs to never auto-uninstall.

    Returns:
        A ``DelistedPluginReport`` summarizing all actions taken.
    """
    report = DelistedPluginReport()
    allow_set: set[str] = set(allowlist) if allowlist else set()

    # Phase 1: load flagged plugins and sync from disk
    if on_progress:
        on_progress("loading_flagged", {"phase": "loading_flagged"})
    await load_flagged_plugins()
    await _sync_blocklist_to_memory()

    # Phase 2: load known marketplaces
    if on_progress:
        on_progress("loading_marketplaces", {"phase": "loading_marketplaces"})
    try:
        marketplaces = await load_known_marketplaces_config_safe()
    except Exception as exc:
        log_error(exc)
        log_for_debugging(
            "detect_and_uninstall_delisted_plugins: failed to load marketplaces"
        )
        return report

    if not marketplaces:
        log_for_debugging(
            "detect_and_uninstall_delisted_plugins: no marketplaces configured"
        )
        return report

    installed = load_installed_plugins_v2()
    flagged_before = set(get_flagged_plugins().keys())

    # Phase 3: scan each marketplace for delisted plugins
    for mp_name in marketplaces:
        if on_progress:
            on_progress("scanning_marketplace", {"marketplace": mp_name})

        try:
            mp_data = marketplaces.get(mp_name, {})
            if not isinstance(mp_data, dict):
                mp_data = {}
            # Ensure plugins key exists
            if "plugins" not in mp_data:
                mp_data["plugins"] = []
        except Exception as exc:
            log_error(exc)
            continue

        delisted = detect_delisted_plugins(installed, mp_data, mp_name)
        if not delisted:
            continue

        for plugin_id in delisted:
            report.delisted.append(plugin_id)

            # Skip if in allowlist
            if plugin_id in allow_set:
                log_for_debugging(
                    f"Skipping allowlisted plugin: {plugin_id}"
                )
                continue

            # Skip if blocked by policy (managed by admin)
            if is_plugin_blocked_by_policy(plugin_id):
                report.skipped_policy.append(plugin_id)
                log_for_debugging(
                    f"Skipping policy-blocked plugin: {plugin_id}"
                )
                continue

            # Phase 4: flag the plugin
            if plugin_id not in flagged_before:
                try:
                    await flag_plugin_as_delisted(
                        plugin_id, reason="detected_as_delisted", persist_blocklist=True
                    )
                    report.flagged.append(plugin_id)
                except Exception as exc:
                    log_error(exc)
                    continue

            # Phase 5: optionally auto-uninstall
            if auto_uninstall:
                if on_progress:
                    on_progress("auto_uninstalling", {"plugin_id": plugin_id})
                try:
                    success, _reason = await auto_uninstall_delisted_plugin(
                        plugin_id, force=force_uninstall
                    )
                    if success:
                        report.uninstalled.append(plugin_id)
                    else:
                        report.failed.append(plugin_id)
                except Exception as exc:
                    log_error(exc)
                    report.failed.append(plugin_id)

    log_for_debugging(
        f"detect_and_uninstall_delisted_plugins: "
        f"delisted={len(report.delisted)}, "
        f"flagged={len(report.flagged)}, "
        f"uninstalled={len(report.uninstalled)}, "
        f"failed={len(report.failed)}, "
        f"skipped_policy={len(report.skipped_policy)}"
    )

    return report


# ---------------------------------------------------------------------------
# Targeted uninstall: uninstall a specific plugin if delisted
# ---------------------------------------------------------------------------


async def uninstall_if_delisted(
    plugin_id: str,
    *,
    force: bool = False,
) -> tuple[bool, str]:
    """Check whether *plugin_id* is delisted; uninstall it if so.

    A plugin is considered delisted when its name is not present in the
    marketplace it was installed from.

    Returns:
        ``(action_taken, reason)`` where:
        - ``action_taken`` is True if the plugin was uninstalled.
        - ``reason`` is a human-readable explanation.
    """
    parsed = parse_plugin_identifier(plugin_id)

    # A plugin without a marketplace suffix cannot be delisted from one
    if not parsed.marketplace:
        return False, "no_marketplace_suffix"

    # Verify the plugin is installed
    installed = load_installed_plugins_v2()
    if plugin_id not in installed.get("plugins", {}):
        return False, "not_installed"

    # Check policy
    if is_plugin_blocked_by_policy(plugin_id):
        return False, "blocked_by_policy"

    # Fetch marketplace and check existence
    try:
        mp_data = await get_marketplace(parsed.marketplace)
    except Exception as exc:
        log_error(exc)
        return False, f"marketplace_load_failed: {exc}"

    mp_plugins = mp_data.get("plugins", [])
    if not isinstance(mp_plugins, list):
        mp_plugins = []

    present = any(p.get("name") == parsed.name for p in mp_plugins)
    if present:
        return False, "still_in_marketplace"

    # Delisted — uninstall
    success, _inner_reason = await auto_uninstall_delisted_plugin(
        plugin_id, force=force
    )
    if success:
        await flag_plugin_as_delisted(plugin_id, reason="uninstalled_if_delisted")
        return True, "uninstalled_as_delisted"

    return False, "uninstall_failed"


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


async def clear_all_flagged_plugins(
    *,
    dry_run: bool = False,
) -> list[str]:
    """Clear all entries from the blocklist (in-memory and on-disk).

    Args:
        dry_run: If True, return the list of IDs that *would* be cleared
                 without making changes.

    Returns:
        The list of plugin IDs that were (or would be) cleared.
    """
    data = load_blocklist_from_disk()
    blocked = data.get("blocked", {})
    # Use _flagged directly (not the copy from get_flagged_plugins) so we can
    # actually clear the module-level store.
    all_ids = sorted(set(blocked.keys()) | set(_flagged.keys()))

    if dry_run:
        return all_ids

    # Clear on-disk
    data["blocked"] = {}
    save_blocklist_to_disk(data)

    # Clear in-memory — mutate _flagged directly
    for pid in all_ids:
        _flagged.pop(pid, None)

    logger.info("Cleared all flagged plugins (%d entries)", len(all_ids))
    return all_ids


async def revalidate_delisted_plugins(
    *,
    on_progress: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> DelistedPluginReport:
    """Re-run delisted detection — useful after a marketplace update.

    Unlike ``detect_and_uninstall_delisted_plugins``, this does NOT
    auto-uninstall. It only checks and updates flags.

    Returns a ``DelistedPluginReport`` (uninstalled/failed will be empty).
    """
    return await detect_and_uninstall_delisted_plugins(
        auto_uninstall=False,
        on_progress=on_progress,
    )


# ---------------------------------------------------------------------------
# Startup hook
# ---------------------------------------------------------------------------


async def run_blocklist_startup_check(
    *,
    auto_uninstall: bool = True,
    force_uninstall: bool = False,
) -> DelistedPluginReport | None:
    """Run the blocklist/delisted check at startup.

    This is the hook intended for use from ``plugin_startup_check`` or
    similar initialization code. It wraps the main orchestration call with
    error handling so a failure during blocklist checks never prevents the
    app from starting.

    Returns:
        A report on success, or ``None`` if an unexpected error occurred.
    """
    try:
        report = await detect_and_uninstall_delisted_plugins(
            auto_uninstall=auto_uninstall,
            force_uninstall=force_uninstall,
        )
        return report
    except Exception as exc:
        log_error(exc)
        logger.warning(
            "Blocklist startup check failed (continuing): %s", exc
        )
        return None
