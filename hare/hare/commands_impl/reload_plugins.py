"""
/reload-plugins command - reload all plugins from disk.

Port of: src/commands/reload-plugins/ (2 files)

Invalidates every in-memory plugin cache, re-reads installed_plugins.json,
reloads plugin manifests from the filesystem, and bumps the MCP reconnection
key in AppState so MCP clients reinitialize.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from hare.state.app_state import get_app_state, set_app_state
from hare.utils.plugins.cache_utils import clear_all_caches
from hare.utils.plugins.installed_plugins_manager import load_installed_plugins_from_disk
from hare.utils.plugins.load_plugin_agents import clear_plugin_agent_cache
from hare.utils.plugins.load_plugin_commands import clear_plugin_command_cache
from hare.utils.plugins.load_plugin_hooks import clear_plugin_hook_cache
from hare.utils.plugins.load_plugin_output_styles import clear_plugin_output_style_cache
from hare.utils.plugins.orphaned_plugin_filter import clear_plugin_cache_exclusions
from hare.utils.plugins.plugin_loader import load_plugins
from hare.utils.plugins.plugin_options_storage import clear_plugin_options_cache

logger = logging.getLogger(__name__)

COMMAND_NAME = "reload-plugins"
DESCRIPTION = "Reload all plugins from disk"
ALIASES: list[str] = []


@dataclass
class _ReloadResult:
    total: int = 0
    enabled: int = 0
    disabled: int = 0
    loaded_from_disk: int = 0
    errors: list[str] = field(default_factory=list)
    mcp_reconnect_key: int = 0


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Reload all plugins from disk: invalidate caches, re-read installed_plugins.json,
    reload manifests, bump MCP reconnect key."""
    _ = args
    r = _ReloadResult()

    # ---- 1. Invalidate all in-memory plugin caches ----
    try:
        clear_plugin_command_cache()
        clear_plugin_agent_cache()
        clear_plugin_hook_cache()
        clear_plugin_output_style_cache()
        clear_plugin_cache_exclusions()
        clear_plugin_options_cache()
        clear_all_caches()
    except Exception as exc:
        logger.warning("Error clearing plugin caches: %s", exc, exc_info=True)
        r.errors.append(f"Cache invalidation error: {exc}")

    # ---- 2. Re-read installed_plugins.json from disk ----
    installed: list[dict[str, Any]] = []
    try:
        data = load_installed_plugins_from_disk()
        installed = list(data.get("plugins", {}).values())
    except Exception as exc:
        logger.error("Failed to read installed_plugins.json: %s", exc, exc_info=True)
        r.errors.append(f"Failed to read installed plugins: {exc}")

    # ---- 3. Reload plugin manifests from the filesystem ----
    loaded: list[dict[str, Any]] = []
    try:
        loaded = load_plugins()
        r.loaded_from_disk = len(loaded)
    except Exception as exc:
        logger.error("Failed to load plugins from disk: %s", exc, exc_info=True)
        r.errors.append(f"Failed to load plugins from disk: {exc}")

    # ---- 4. Bump MCP reconnection key ----
    try:

        def _updater(state: Any) -> Any:
            return replace(
                state, mcp_plugin_reconnect_key=state.mcp_plugin_reconnect_key + 1
            )

        set_app_state(_updater)
        r.mcp_reconnect_key = get_app_state().mcp_plugin_reconnect_key
    except Exception as exc:
        logger.warning("Failed to bump reconnect key: %s", exc, exc_info=True)
        r.errors.append(f"MCP reconnect key bump failed: {exc}")

    # ---- 5. Compute enabled / disabled counts ----
    enabled_names: set[str] = {
        str(e.get("name", "")) for e in installed if e.get("enabled", True)
    }
    r.total = len(installed)
    r.enabled = len(enabled_names)
    r.disabled = r.total - r.enabled

    # ---- 6. Build response ----
    lines: list[str] = []
    if r.errors:
        lines.append("## Plugins Reloaded -- With Errors\n")
        for err in r.errors:
            lines.append(f"- Error: {err}")
        lines.append("")
    else:
        lines.append("## Plugins Reloaded\n")

    if r.total == 0:
        lines.append("No plugins installed. Use `/plugin install <name>` to add one.")
    else:
        lines.append(
            f"- **{r.enabled}** enabled, **{r.disabled}** disabled "
            f"({r.total} total)"
        )
        if r.loaded_from_disk:
            lines.append(f"- **{r.loaded_from_disk}** manifest(s) loaded from disk")

    manifest_names = sorted(str(p.get("name", "")) for p in loaded if p.get("name"))
    if manifest_names:
        lines.append("\n### Loaded manifests")
        for name in manifest_names:
            status = "enabled" if name in enabled_names else "disabled"
            lines.append(f"- `{name}` ({status})")

    if r.mcp_reconnect_key > 0:
        lines.append(
            f"\nMCP plugin reconnection key: `{r.mcp_reconnect_key}` "
            "(MCP clients will reinitialize)"
        )

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
