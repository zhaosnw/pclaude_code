"""
Plugin cache invalidation and orphaned version GC.

Port of: src/utils/plugins/cacheUtils.ts
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from hare.utils.debug import log_for_debugging
from hare.utils.errors import get_errno_code

ORPHANED_AT_FILENAME = ".orphaned_at"
CLEANUP_AGE_MS = 7 * 24 * 60 * 60 * 1000  # 7 days


# ---------------------------------------------------------------------------
# Plugin cache path (derived from plugin_directories)
# ---------------------------------------------------------------------------


def _get_plugin_cache_path() -> str:
    """Return the cache directory under the plugins directory."""
    from hare.utils.plugins.plugin_directories import get_plugins_directory

    return str(Path(get_plugins_directory()) / "cache")


# ---------------------------------------------------------------------------
# Cache invalidation — plugin-level
# ---------------------------------------------------------------------------


def clear_all_plugin_caches() -> None:
    """Invalidate all plugin-layer caches: loader, commands, agents, hooks,
    options, output styles, and the global output-styles merge.

    Also prunes hooks from plugins no longer in the enabled set so
    uninstalled/disabled plugins stop firing immediately (gh-36995).
    Prune-only: hooks from newly-enabled plugins are NOT added here —
    they wait for /reload-plugins like commands/agents/MCP do.
    Fire-and-forget: old hooks stay valid until the prune completes.
    """
    # Plugin loader cache
    try:
        from hare.utils.plugins.plugin_loader import clear_plugin_cache
    except ImportError:
        def clear_plugin_cache() -> None:
            pass

    clear_plugin_cache()

    # Command cache
    try:
        from hare.utils.plugins.load_plugin_commands import clear_plugin_command_cache
    except ImportError:
        def clear_plugin_command_cache() -> None:
            pass

    clear_plugin_command_cache()

    # Agent cache
    try:
        from hare.utils.plugins.load_plugin_agents import clear_plugin_agent_cache
    except ImportError:
        def clear_plugin_agent_cache() -> None:
            pass

    clear_plugin_agent_cache()

    # Hook cache
    try:
        from hare.utils.plugins.load_plugin_hooks import clear_plugin_hook_cache
    except ImportError:
        def clear_plugin_hook_cache() -> None:
            pass

    clear_plugin_hook_cache()

    # Prune removed hooks (fire-and-forget — errors logged but never raised)
    try:
        from hare.utils.plugins.load_plugin_hooks import prune_removed_plugin_hooks
    except ImportError:
        async def _noop_prune() -> None:
            pass
        prune_removed_plugin_hooks = _noop_prune  # type: ignore[assignment]

    def _schedule_prune() -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(prune_removed_plugin_hooks())  # type: ignore[arg-type]
        except RuntimeError:
            # No running event loop — schedule via asyncio.run when possible
            try:
                asyncio.run(prune_removed_plugin_hooks())  # type: ignore[arg-type]
            except Exception as e:
                log_for_debugging(f"prune_removed_plugin_hooks failed: {e}")

    _schedule_prune()

    # Options cache
    try:
        from hare.utils.plugins.plugin_options_storage import clear_plugin_options_cache
    except ImportError:
        def clear_plugin_options_cache() -> None:
            pass

    clear_plugin_options_cache()

    # Output style cache
    try:
        from hare.utils.plugins.load_plugin_output_styles import clear_plugin_output_style_cache
    except ImportError:
        def clear_plugin_output_style_cache() -> None:
            pass

    clear_plugin_output_style_cache()

    # Global output styles cache (merged across plugins)
    try:
        from hare.constants.output_styles import clear_all_output_styles_cache
    except ImportError:
        def clear_all_output_styles_cache() -> None:
            pass

    clear_all_output_styles_cache()


# ---------------------------------------------------------------------------
# Cache invalidation — broad (plugins + CLI + agents + skills)
# ---------------------------------------------------------------------------


def clear_all_caches() -> None:
    """Clear plugin caches plus commands/agents/skills caches."""
    clear_all_plugin_caches()

    # CLI commands cache
    try:
        from hare.commands import clear_commands_cache
    except ImportError:
        def clear_commands_cache() -> None:
            pass

    clear_commands_cache()

    # Agent definitions cache
    try:
        from hare.tools.AgentTool.load_agents_dir import clear_agent_definitions_cache
    except ImportError:
        def clear_agent_definitions_cache() -> None:
            pass

    clear_agent_definitions_cache()

    # Skill prompt cache
    try:
        from hare.tools.SkillTool.prompt import clear_prompt_cache
    except ImportError:
        def clear_prompt_cache() -> None:
            pass

    clear_prompt_cache()

    # Sent skill names (prevents duplicate auto-attachment)
    try:
        from hare.utils.attachments import reset_sent_skill_names
    except ImportError:
        def reset_sent_skill_names() -> None:
            pass

    reset_sent_skill_names()


# ---------------------------------------------------------------------------
# Orphaned version marking
# ---------------------------------------------------------------------------


def _get_orphaned_at_path(version_path: str) -> str:
    """Return the path to the .orphaned_at marker inside *version_path*."""
    return str(Path(version_path) / ORPHANED_AT_FILENAME)


async def _remove_orphaned_at_marker(version_path: str) -> None:
    """Delete the .orphaned_at marker if it exists.  Silently skip ENOENT."""
    orphaned_at_path = _get_orphaned_at_path(version_path)
    try:
        await asyncio.to_thread(lambda: Path(orphaned_at_path).unlink(missing_ok=True))
    except OSError as e:
        code = get_errno_code(e)
        if code == "ENOENT":
            return
        log_for_debugging(
            f"Failed to remove .orphaned_at: {version_path}: {e}"
        )


async def mark_plugin_version_orphaned(version_path: str) -> None:
    """Mark a plugin version as orphaned by writing a timestamp file.

    Called when a plugin is uninstalled or updated to a new version.
    """
    p = Path(version_path) / ORPHANED_AT_FILENAME
    try:
        await asyncio.to_thread(
            p.write_text, str(int(time.time() * 1000)), encoding="utf-8"
        )
    except OSError as e:
        log_for_debugging(f"Failed to write .orphaned_at: {version_path}: {e}")


# ---------------------------------------------------------------------------
# Helper — read subdirectory names
# ---------------------------------------------------------------------------


async def _read_subdirs(dir_path: str) -> list[str]:
    """Return the names of subdirectories inside *dir_path*.

    Returns an empty list when the directory does not exist or cannot be read.
    """
    p = Path(dir_path)
    if not p.is_dir():
        return []
    try:
        entries = await asyncio.to_thread(lambda: list(p.iterdir()))
    except OSError:
        return []
    return [entry.name for entry in entries if entry.is_dir()]


# ---------------------------------------------------------------------------
# Helper — remove empty directories (only if no subdirs remain)
# ---------------------------------------------------------------------------


async def _remove_if_empty(dir_path: str) -> None:
    """Remove *dir_path* if it contains no subdirectories."""
    if not await _read_subdirs(dir_path):
        try:
            await asyncio.to_thread(
                lambda: Path(dir_path).rmdir()
            )
        except OSError as e:
            log_for_debugging(f"Failed to remove empty dir: {dir_path}: {e}")


# ---------------------------------------------------------------------------
# Helper — gather installed version paths from persisted state
# ---------------------------------------------------------------------------


def _get_installed_version_paths() -> Optional[set[str]]:
    """Build a set of install paths for every currently-installed plugin version.

    Returns ``None`` when the installed-plugins data cannot be loaded (the
    caller should abort cleanup so stale data does not cause accidental
    deletion).
    """
    try:
        from hare.utils.plugins.installed_plugins_manager import (
            load_installed_plugins_from_disk,
        )

        disk_data = load_installed_plugins_from_disk()
    except Exception as e:
        log_for_debugging(f"Failed to load installed plugins: {e}")
        return None

    paths: set[str] = set()
    plugins = disk_data.get("plugins", {}) if isinstance(disk_data, dict) else {}
    for installations in plugins.values():
        if isinstance(installations, dict):
            ip = installations.get("install_path") or installations.get("installPath", "")
            if ip:
                paths.add(ip)
        elif isinstance(installations, list):
            for entry in installations:
                if isinstance(entry, dict):
                    ip = entry.get("install_path") or entry.get("installPath", "")
                    if ip:
                        paths.add(ip)
    return paths


# ---------------------------------------------------------------------------
# Helper — process a single orphaned version (mark or delete)
# ---------------------------------------------------------------------------


async def _process_orphaned_plugin_version(version_path: str, now: float) -> None:
    """Inspect a cached plugin version not present in the installed set.

    * If no ``.orphaned_at`` marker exists, create one (handles upgrades from
      older CC versions and manual edits to installed_plugins.json).
    * If the marker exists and is older than ``CLEANUP_AGE_MS``, delete the
      entire version directory.
    """
    orphaned_at_path = _get_orphaned_at_path(version_path)
    p = Path(orphaned_at_path)

    orphaned_at: float
    try:
        st = await asyncio.to_thread(p.stat)
        orphaned_at = st.st_mtime * 1000  # convert seconds → milliseconds
    except OSError as e:
        code = get_errno_code(e)
        if code == "ENOENT":
            # No marker yet — create one now so future runs can age it out
            await mark_plugin_version_orphaned(version_path)
            return
        log_for_debugging(
            f"Failed to stat orphaned marker: {version_path}: {e}"
        )
        return

    if now - orphaned_at > CLEANUP_AGE_MS:
        try:
            import shutil
            await asyncio.to_thread(
                shutil.rmtree, version_path, True  # ignore_errors=True
            )
        except Exception as e:
            log_for_debugging(
                f"Failed to delete orphaned version: {version_path}: {e}"
            )


# ---------------------------------------------------------------------------
# Background GC — full two-pass cleanup
# ---------------------------------------------------------------------------


async def cleanup_orphaned_plugin_versions_in_background() -> None:
    """Remove stale orphaned plugin directories after a 7-day grace period.

    **Pass 1** — Remove ``.orphaned_at`` markers from installed versions.
    This handles cases where a plugin was reinstalled after being orphaned.

    **Pass 2** — For each cached version **not** in ``installed_plugins.json``:

    * If no ``.orphaned_at`` marker exists, create one (handles upgrades from
      older CC versions and manual edits to installed_plugins.json).
    * If the marker exists and is older than 7 days, delete the entire version
      directory along with empty parent (plugin / marketplace) directories.

    Zip-cache mode stores plugins as ``.zip`` files, not directories.
    ``_read_subdirs`` filters to directories only, so ``_remove_if_empty``
    would treat plugin dirs as empty and delete them (including the ZIPs).
    In that case we skip cleanup entirely.
    """
    try:
        from hare.utils.plugins.zip_cache import is_plugin_zip_cache_enabled
    except ImportError:
        is_plugin_zip_cache_enabled = lambda: False  # type: ignore[assignment,no-redef]

    if is_plugin_zip_cache_enabled():
        return

    try:
        installed_versions = _get_installed_version_paths()
        if installed_versions is None:
            return

        cache_path = _get_plugin_cache_path()
        now = time.time() * 1000  # milliseconds, matching JS Date.now()

        # ---- Pass 1: clear orphan markers on still-installed versions ----
        await asyncio.gather(
            *(_remove_orphaned_at_marker(p) for p in installed_versions),
            return_exceptions=True,
        )

        # ---- Pass 2: process every cached version not in the installed set ----
        for marketplace in await _read_subdirs(cache_path):
            marketplace_path = str(Path(cache_path) / marketplace)

            for plugin in await _read_subdirs(marketplace_path):
                plugin_path = str(Path(marketplace_path) / plugin)

                for version in await _read_subdirs(plugin_path):
                    version_path = str(Path(plugin_path) / version)
                    if version_path in installed_versions:
                        continue
                    await _process_orphaned_plugin_version(version_path, now)

                # Clean up empty plugin directory
                await _remove_if_empty(plugin_path)

            # Clean up empty marketplace directory
            await _remove_if_empty(marketplace_path)

    except Exception as e:
        log_for_debugging(f"Plugin cache cleanup failed: {e}")
