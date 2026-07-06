"""Reload active plugin components in-session. Port of refresh.ts."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from hare.bootstrap.state import get_original_cwd, clear_registered_plugin_hooks
from hare.utils.debug import log_for_debugging
from hare.utils.errors import error_message
from hare.utils.log import log_error
from hare.utils.plugins.cache_utils import clear_all_caches
from hare.utils.plugins.dependency_resolver import (
    LoadedPlugin,
    PluginError,
    verify_and_demote,
)
from hare.utils.plugins.installed_plugins_manager import (
    is_plugin_enabled,
    load_installed_plugins_v2,
)
from hare.utils.plugins.load_plugin_commands import (
    clear_plugin_command_cache, get_plugin_commands,
)
from hare.utils.plugins.load_plugin_agents import (
    clear_plugin_agent_cache, load_plugin_agents,
)
from hare.utils.plugins.load_plugin_hooks import (
    clear_plugin_hook_cache, prune_removed_plugin_hooks, load_plugin_hooks,
)
from hare.utils.plugins.load_plugin_output_styles import clear_plugin_output_style_cache
from hare.utils.plugins.mcp_plugin_integration import load_plugin_mcp_servers
from hare.utils.plugins.lsp_plugin_integration import load_plugin_lsp_servers
from hare.utils.plugins.orphaned_plugin_filter import clear_plugin_cache_exclusions
from hare.utils.plugins.plugin_directories import get_plugins_directory
from hare.utils.plugins.plugin_loader import load_plugins, find_plugin
from hare.utils.plugins.plugin_policy import is_plugin_blocked_by_policy
from hare.utils.plugins.plugin_validator import validate_plugin_manifest

logger = logging.getLogger(__name__)


@dataclass
class RefreshActivePluginsResult:
    enabled_count: int = 0
    disabled_count: int = 0
    command_count: int = 0
    agent_count: int = 0
    hook_count: int = 0
    mcp_count: int = 0
    lsp_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    agent_definitions: Any = None
    plugin_commands: list[Any] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    dependency_errors: list[PluginError] = field(default_factory=list)
    newly_enabled: list[str] = field(default_factory=list)
    newly_disabled: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass
class RefreshOptions:
    """Configuration knobs for refresh behaviour."""

    verify_dependencies: bool = True
    validate_manifests: bool = True
    run_startup_checks: bool = True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _merge_errors(existing: list[str], fresh: list[str]) -> list[str]:
    """Preserve lsp-manager / plugin: errors, dedup against fresh."""
    preserved = [e for e in existing if e.startswith(("lsp-manager:", "plugin:"))]
    fresh_set = set(fresh)
    return [e for e in preserved if e not in fresh_set] + fresh


def _count_hooks(plugins: list[dict[str, Any]]) -> int:
    """Tally hooks from enabled plugin hook configs."""
    total = 0
    for p in plugins:
        cfg = p.get("hooksConfig") or p.get("hooks")
        if not isinstance(cfg, dict):
            continue
        for matchers in cfg.values():
            if not isinstance(matchers, list):
                continue
            for m in matchers:
                if isinstance(m, dict):
                    total += len(m.get("hooks", []) or [])
                elif hasattr(m, "hooks"):
                    total += len(m.hooks or [])
    return total


def _classify(plugins: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split plugins into enabled / disabled (respects policy + per-plugin flag)."""
    enabled: list[dict[str, Any]] = []
    disabled: list[dict[str, Any]] = []
    for p in plugins:
        name = p.get("name", "")
        if not name:
            continue
        if is_plugin_blocked_by_policy(name) or not is_plugin_enabled(name):
            disabled.append(p)
        else:
            enabled.append(p)
    return enabled, disabled


def _validate_all(enabled: list[dict[str, Any]]) -> list[str]:
    """Run manifest validation on every enabled plugin; return deduped issues."""
    seen: set[str] = set()
    issues: list[str] = []
    for p in enabled:
        name = p.get("name", "?")
        manifest = p.get("manifest")
        if not isinstance(manifest, dict):
            key = f"{name}: missing or invalid manifest"
            if key not in seen:
                seen.add(key)
                issues.append(key)
            continue
        for err in validate_plugin_manifest(manifest):
            key = f"{name}: {err}"
            if key not in seen:
                seen.add(key)
                issues.append(key)
    return issues


def _build_loaded_plugin(p: dict[str, Any]) -> LoadedPlugin:
    return LoadedPlugin(
        source=p.get("source", p.get("name", "")),
        name=p.get("name", ""),
        enabled=True,
        manifest=p.get("manifest") or {},
    )


def _detect_enablement_changes(
    previous_enabled: set[str], current_enabled: set[str],
) -> tuple[list[str], list[str]]:
    newly_enabled = sorted(current_enabled - previous_enabled)
    newly_disabled = sorted(previous_enabled - current_enabled)
    return newly_enabled, newly_disabled


def _get_previous_enabled_names(
    get_app_state: Callable[[], Any] | None,
) -> set[str]:
    if get_app_state is None:
        installed = load_installed_plugins_v2().get("plugins", {})
        return {n for n, e in installed.items() if e.get("enabled", True)}
    try:
        state = get_app_state()
    except Exception:
        return set()
    prev_plugins = state.get("plugins", {}) if isinstance(state, dict) else {}
    prev_enabled = prev_plugins.get("enabled", []) if isinstance(prev_plugins, dict) else []
    return {p.get("name", "") for p in prev_enabled if isinstance(p, dict)}


def _sanity_check(
    result: RefreshActivePluginsResult, enabled: list[dict[str, Any]],
) -> list[str]:
    """Post-refresh consistency checks; returns non-fatal warnings."""
    warnings: list[str] = []
    if result.enabled_count > 0 and result.command_count == 0:
        warnings.append(
            f"{result.enabled_count} plugins enabled but 0 commands loaded"
        )
    if result.hook_count == 0 and any(
        p.get("manifest", {}).get("hooks") for p in enabled
    ):
        warnings.append("Plugins declare hooks but 0 hooks were loaded")
    if result.error_count > result.enabled_count:
        warnings.append(
            f"Error count ({result.error_count}) exceeds enabled plugin count "
            f"({result.enabled_count})"
        )
    return warnings


def _reinit_lsp() -> None:
    """Best-effort re-init of LSP manager — no-op if never started."""
    try:
        from hare.services.lsp.manager import reinitialize_lsp_server_manager
        reinitialize_lsp_server_manager()
    except ImportError:
        pass
    except Exception as exc:
        log_for_debugging(f"LSP reinit failed: {error_message(exc)}")


# ---------------------------------------------------------------------------
# Server loaders
# ---------------------------------------------------------------------------


async def _load_servers(
    enabled: list[dict[str, Any]], errors: list[str], *, kind: str,
) -> int:
    """Load MCP or LSP servers for enabled plugins, cache on dict, return count."""
    key = "mcpServers" if kind == "mcp" else "lspServers"
    loader = load_plugin_mcp_servers if kind == "mcp" else load_plugin_lsp_servers
    total = 0
    for p in enabled:
        name = p.get("name", "?")
        try:
            existing = p.get(key)
            if isinstance(existing, (dict, list)):
                total += len(existing)
                continue
            servers = await loader(p.get("path", ""))
            if servers:
                p[key] = servers
                total += len(servers)
        except Exception as exc:
            msg = f"{kind.upper()} load failed for '{name}': {error_message(exc)}"
            logger.warning(msg)
            errors.append(msg)
    return total


async def _load_servers_concurrent(
    enabled: list[dict[str, Any]], errors: list[str],
) -> tuple[int, int]:
    """Load MCP and LSP servers concurrently. Returns (mcp_count, lsp_count)."""
    results = await asyncio.gather(
        _load_servers(enabled, errors, kind="mcp"),
        _load_servers(enabled, errors, kind="lsp"),
        return_exceptions=True,
    )
    mcp_count = results[0] if isinstance(results[0], int) else 0
    lsp_count = results[1] if isinstance(results[1], int) else 0
    for i, label in enumerate(("MCP", "LSP")):
        if isinstance(results[i], BaseException):
            msg = f"{label} concurrent load failed: {error_message(results[i])}"
            logger.error(msg)
            errors.append(msg)
    return mcp_count, lsp_count


# ---------------------------------------------------------------------------
# Main refresh
# ---------------------------------------------------------------------------


async def refresh_active_plugins(
    set_app_state: Callable[[Any], Any],
    *,
    options: RefreshOptions | None = None,
    get_app_state: Callable[[], Any] | None = None,
) -> RefreshActivePluginsResult:
    """Refresh commands, agents, hooks, MCP, LSP from disk; pushes to AppState."""
    opts = options or RefreshOptions()
    t0 = time.monotonic()
    log_for_debugging("refreshActivePlugins: clearing all plugin caches")

    # 1. Clear all in-memory caches
    clear_all_caches()
    clear_plugin_cache_exclusions()
    for fn in (clear_plugin_command_cache, clear_plugin_agent_cache,
               clear_plugin_hook_cache, clear_plugin_output_style_cache):
        fn()

    # 2. Re-read plugins from disk, classify
    plugins_dir = get_plugins_directory()
    enabled, disabled = _classify(load_plugins(plugins_dir))

    # 3. Validate manifests
    validation_issues: list[str] = []
    if opts.validate_manifests and enabled:
        validation_issues = _validate_all(enabled)
        if validation_issues:
            logger.warning(
                "refreshActivePlugins: %d validation issue(s): %s",
                len(validation_issues), "; ".join(validation_issues[:5]),
            )

    # 4. Dependency health verification
    dep_errors: list[PluginError] = []
    if opts.verify_dependencies and enabled:
        loaded = [_build_loaded_plugin(p) for p in enabled]
        demoted, dep_errors = verify_and_demote(loaded)
        if demoted:
            demoted_names = {d for d in demoted}
            newly_disabled_deps = [p for p in enabled if p.get("name", "") in demoted_names]
            enabled = [p for p in enabled if p.get("name", "") not in demoted_names]
            disabled = disabled + newly_disabled_deps
            logger.warning(
                "refreshActivePlugins: %d plugin(s) demoted due to unmet deps: %s",
                len(demoted), ", ".join(sorted(demoted_names)),
            )

    # 5. Load commands + agent definitions
    plugin_commands = get_plugin_commands()
    agent_definitions: Any = None
    try:
        agent_definitions = load_plugin_agents(get_original_cwd())
    except Exception as exc:
        log_error(exc)
        log_for_debugging(f"refreshActivePlugins: agent load failed: {error_message(exc)}")

    agent_count = len(agent_definitions) if isinstance(agent_definitions, list) else 0
    errors: list[str] = list(validation_issues)

    # 6. Load MCP + LSP servers concurrently
    mcp_count, lsp_count = await _load_servers_concurrent(enabled, errors)

    # 7. Detect enablement changes
    previous_enabled = _get_previous_enabled_names(get_app_state)
    current_enabled_names = {p.get("name", "") for p in enabled if p.get("name")}
    newly_enabled, newly_disabled = _detect_enablement_changes(
        previous_enabled, current_enabled_names,
    )

    # 8. Push updated state into AppState
    set_app_state(
        lambda prev: {
            **prev,
            "plugins": {
                **prev.get("plugins", {}),
                "enabled": enabled,
                "disabled": disabled,
                "commands": plugin_commands,
                "errors": _merge_errors(
                    prev.get("plugins", {}).get("errors", []), errors,
                ),
                "needsRefresh": False,
            },
            "agentDefinitions": agent_definitions,
            "mcp": {
                **prev.get("mcp", {}),
                "pluginReconnectKey": prev.get("mcp", {}).get("pluginReconnectKey", 0) + 1,
            },
        }
    )

    # 9. Re-init LSP, then swap hooks
    _reinit_lsp()
    clear_registered_plugin_hooks()
    hook_load_failed = False
    try:
        await prune_removed_plugin_hooks()
        await load_plugin_hooks(plugins_dir)
    except Exception as exc:
        hook_load_failed = True
        log_error(exc)
        log_for_debugging(f"refreshActivePlugins: hook load failed: {error_message(exc)}")

    hook_count = _count_hooks(enabled)

    # 10. Run startup checks for newly-enabled plugins
    if opts.run_startup_checks and newly_enabled:
        try:
            from hare.utils.plugins.plugin_startup_check import run_plugin_startup_checks
            await run_plugin_startup_checks()
        except Exception as exc:
            log_for_debugging(
                f"refreshActivePlugins: startup checks failed: {error_message(exc)}"
            )

    # 11. Build result + sanity-check
    elapsed_ms = (time.monotonic() - t0) * 1000
    result = RefreshActivePluginsResult(
        enabled_count=len(enabled),
        disabled_count=len(disabled),
        command_count=len(plugin_commands),
        agent_count=agent_count,
        hook_count=hook_count,
        mcp_count=mcp_count,
        lsp_count=lsp_count,
        error_count=len(errors) + (1 if hook_load_failed else 0),
        errors=errors,
        agent_definitions=agent_definitions,
        plugin_commands=list(plugin_commands),
        validation_errors=validation_issues,
        dependency_errors=dep_errors,
        newly_enabled=newly_enabled,
        newly_disabled=newly_disabled,
        elapsed_ms=elapsed_ms,
    )

    for w in _sanity_check(result, enabled):
        log_for_debugging(f"refreshActivePlugins sanity: {w}")

    log_for_debugging(
        f"refreshActivePlugins: {len(enabled)} enabled, {len(plugin_commands)} cmds, "
        f"{agent_count} agents, {hook_count} hooks, {mcp_count} MCP, {lsp_count} LSP "
        f"({elapsed_ms:.1f}ms)"
    )

    return result


# ---------------------------------------------------------------------------
# Single-plugin targeted refresh
# ---------------------------------------------------------------------------


async def refresh_plugin_by_name(
    plugin_name: str,
    set_app_state: Callable[[Any], Any],
    *,
    get_app_state: Callable[[], Any] | None = None,
) -> RefreshActivePluginsResult | None:
    """Refresh a single plugin by name; returns None if not found on disk."""
    plugins_dir = get_plugins_directory()
    plugin = find_plugin(plugin_name, plugins_dir)
    if plugin is None:
        log_for_debugging(f"refresh_plugin_by_name: '{plugin_name}' not found on disk")
        return None

    t0 = time.monotonic()
    enabled, disabled = _classify([plugin])
    is_enabled = len(enabled) == 1
    target = enabled[0] if is_enabled else disabled[0]

    # Validate
    validation_issues: list[str] = []
    manifest = target.get("manifest")
    if isinstance(manifest, dict):
        validation_issues = [
            f"{plugin_name}: {e}" for e in validate_plugin_manifest(manifest)
        ]

    # Clear + reload caches
    for fn in (clear_plugin_command_cache, clear_plugin_agent_cache,
               clear_plugin_hook_cache, clear_plugin_output_style_cache):
        fn()

    plugin_commands = get_plugin_commands()
    agent_definitions: Any = None
    try:
        agent_definitions = load_plugin_agents(get_original_cwd())
    except Exception as exc:
        log_for_debugging(f"refresh_plugin_by_name: agent load failed: {error_message(exc)}")

    agent_count = len(agent_definitions) if isinstance(agent_definitions, list) else 0
    errors: list[str] = list(validation_issues)

    # Servers
    load_target = [target] if is_enabled else []
    mcp_count = await _load_servers(load_target, errors, kind="mcp")
    lsp_count = await _load_servers(load_target, errors, kind="lsp")

    # Enablement delta
    previous_enabled = _get_previous_enabled_names(get_app_state)
    newly_enabled = [plugin_name] if is_enabled and plugin_name not in previous_enabled else []
    newly_disabled = [plugin_name] if not is_enabled and plugin_name in previous_enabled else []

    # Push AppState delta
    set_app_state(
        lambda prev: {
            **prev,
            "plugins": {
                **prev.get("plugins", {}),
                "enabled": _upsert_plugin_list(
                    prev.get("plugins", {}).get("enabled", []), target, keep=is_enabled,
                ),
                "disabled": _upsert_plugin_list(
                    prev.get("plugins", {}).get("disabled", []), target, keep=not is_enabled,
                ),
                "commands": plugin_commands,
                "errors": _merge_errors(
                    prev.get("plugins", {}).get("errors", []), errors,
                ),
                "needsRefresh": False,
            },
            "agentDefinitions": agent_definitions,
            "mcp": {
                **prev.get("mcp", {}),
                "pluginReconnectKey": prev.get("mcp", {}).get("pluginReconnectKey", 0) + 1,
            },
        }
    )

    _reinit_lsp()
    try:
        await prune_removed_plugin_hooks()
        await load_plugin_hooks(plugins_dir)
    except Exception as exc:
        log_for_debugging(f"refresh_plugin_by_name: hook load failed: {error_message(exc)}")

    elapsed_ms = (time.monotonic() - t0) * 1000
    log_for_debugging(
        f"refresh_plugin_by_name: '{plugin_name}' done in {elapsed_ms:.1f}ms "
        f"(enabled={is_enabled})"
    )

    return RefreshActivePluginsResult(
        enabled_count=1 if is_enabled else 0,
        disabled_count=0 if is_enabled else 1,
        command_count=len(plugin_commands),
        agent_count=agent_count,
        hook_count=_count_hooks([target] if is_enabled else []),
        mcp_count=mcp_count,
        lsp_count=lsp_count,
        error_count=len(errors),
        errors=errors,
        agent_definitions=agent_definitions,
        plugin_commands=list(plugin_commands),
        validation_errors=validation_issues,
        newly_enabled=newly_enabled,
        newly_disabled=newly_disabled,
        elapsed_ms=elapsed_ms,
    )


def _upsert_plugin_list(
    plugin_list: list[dict[str, Any]], plugin: dict[str, Any], *, keep: bool,
) -> list[dict[str, Any]]:
    """Insert or remove *plugin* from *plugin_list* by name; returns new list."""
    name = plugin.get("name", "")
    if not name:
        return list(plugin_list)
    filtered = [p for p in plugin_list if p.get("name") != name]
    if keep:
        filtered.append(plugin)
    return filtered
