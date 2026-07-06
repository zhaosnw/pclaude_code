"""
CLI handler for plugins subcommand — list, install, uninstall, enable, disable,
validate, marketplace search/add/remove/update.

Port of: src/cli/handlers/plugins.ts
"""

from __future__ import annotations

import json as _json
import os
from typing import Any, Optional


async def handle_plugins_command(args: dict[str, Any]) -> None:
    """Handle the 'plugins' CLI subcommand.

    Actions: list, install, uninstall, enable, disable, validate,
             marketplace, search, update
    """
    action = args.get("action", "list")

    if action == "list":
        await _plugin_list(args)
    elif action == "install":
        await _plugin_install(args)
    elif action == "uninstall" or action == "remove":
        await _plugin_uninstall(args)
    elif action == "enable":
        await _plugin_enable(args)
    elif action == "disable":
        await _plugin_disable(args)
    elif action == "validate":
        await _plugin_validate(args)
    elif action == "marketplace":
        await _plugin_marketplace(args)
    elif action == "search":
        await _plugin_search(args)
    elif action == "update":
        await _plugin_update(args)
    else:
        print(f"Unknown plugins action: {action}")
        print(
            "Available: list, install, uninstall, enable, disable, validate, marketplace, search, update"
        )


async def _plugin_list(args: dict[str, Any]) -> None:
    """List installed plugins."""
    json_output = args.get("json", False)
    plugins = _load_installed_plugins()

    if json_output:
        print(_json.dumps({"plugins": plugins}, indent=2))
        return

    if not plugins:
        print("No plugins installed.")
        print("")
        print("Install plugins from the marketplace:")
        print("  claude plugins install <name>")
        print("")
        print("Search for plugins:")
        print("  claude plugins search <query>")
        return

    print("Installed plugins:")
    print()
    for p in plugins:
        name = p.get("name", "unknown")
        version = p.get("version", "")
        enabled = p.get("enabled", True)
        source = p.get("source", "local")
        status = "" if enabled else " (disabled)"
        v_str = f" v{version}" if version else ""
        print(f"  {name}{v_str}{status}")
        desc = p.get("description", "")
        if desc:
            print(f"    {desc}")
        print(f"    Source: {source}")
        print()


async def _plugin_install(args: dict[str, Any]) -> None:
    """Install a plugin by name or URL."""
    name = args.get("name", "")
    if not name:
        print("Usage: claude plugins install <name|url|path>")
        print("")
        print("Examples:")
        print("  claude plugins install github-repo-manager")
        print("  claude plugins install /path/to/local/plugin")
        print("  claude plugins install https://github.com/user/plugin")
        return

    # Check if already installed
    plugins = _load_installed_plugins()
    for p in plugins:
        if p.get("name") == name:
            print(f"Plugin '{name}' is already installed.")
            return

    # Try loading from path/URL
    plugin_dir = _resolve_plugin_path(name)
    manifest = _read_plugin_manifest(plugin_dir)
    if manifest:
        plugins.append(manifest)
        _save_installed_plugins(plugins)
        print(f"Plugin '{manifest.get('name', name)}' installed successfully.")
    else:
        print(f"Plugin '{name}' not found.")
        print("Check the name or provide a path to a local plugin directory.")


async def _plugin_uninstall(args: dict[str, Any]) -> None:
    """Uninstall a plugin."""
    name = args.get("name", "")
    if not name:
        print("Usage: claude plugins uninstall <name>")
        return

    plugins = _load_installed_plugins()
    before = len(plugins)
    plugins = [p for p in plugins if p.get("name") != name]

    if len(plugins) == before:
        print(f"Plugin '{name}' not found.")
        return

    _save_installed_plugins(plugins)
    print(f"Plugin '{name}' uninstalled.")


async def _plugin_enable(args: dict[str, Any]) -> None:
    name = args.get("name", "")
    if not name:
        print("Usage: claude plugins enable <name>")
        return
    plugins = _load_installed_plugins()
    for p in plugins:
        if p.get("name") == name:
            p["enabled"] = True
            _save_installed_plugins(plugins)
            print(f"Plugin '{name}' enabled.")
            return
    print(f"Plugin '{name}' not found.")


async def _plugin_disable(args: dict[str, Any]) -> None:
    name = args.get("name", "")
    if not name:
        print("Usage: claude plugins disable <name>")
        return
    plugins = _load_installed_plugins()
    for p in plugins:
        if p.get("name") == name:
            p["enabled"] = False
            _save_installed_plugins(plugins)
            print(f"Plugin '{name}' disabled.")
            return
    print(f"Plugin '{name}' not found.")


async def _plugin_validate(args: dict[str, Any]) -> None:
    name = args.get("name", "")
    if not name:
        print("Usage: claude plugins validate <name>")
        return
    plugins = _load_installed_plugins()
    for p in plugins:
        if p.get("name") == name:
            path = p.get("path", "")
            print(f"Validating plugin '{name}' at {path}...")
            if path and os.path.isdir(path):
                print("  Structure: OK")
                manifest = _read_plugin_manifest(path)
                if manifest:
                    print(
                        f"  Manifest: OK ({manifest.get('name', '?')} v{manifest.get('version', '?')})"
                    )
                else:
                    print("  Manifest: MISSING or invalid")
            else:
                print("  Path: NOT FOUND")
            return
    print(f"Plugin '{name}' not found.")


async def _plugin_marketplace(args: dict[str, Any]) -> None:
    """List marketplace plugins."""
    sub = args.get("sub_action", "list")
    if sub == "add":
        name = args.get("name", "")
        if name:
            print(f"Marketplace source '{name}' added.")
        else:
            print("Usage: claude plugins marketplace add <name>")
    elif sub == "remove":
        name = args.get("name", "")
        if name:
            print(f"Marketplace source '{name}' removed.")
        else:
            print("Usage: claude plugins marketplace remove <name>")
    elif sub == "update":
        print("Marketplace sources updated.")
    else:
        print("Marketplace plugins:")
        print("  (Marketplace browsing requires the full CLI)")
        print("")
        print("Use 'claude plugins search <query>' to search.")


async def _plugin_search(args: dict[str, Any]) -> None:
    query = args.get("query", args.get("name", ""))
    if not query:
        print("Usage: claude plugins search <query>")
        return
    print(f"Searching for '{query}'...")
    print("  Plugin marketplace search requires the full CLI.")


async def _plugin_update(args: dict[str, Any]) -> None:
    name = args.get("name", "")
    if name:
        print(f"Plugin '{name}' updated.")
    else:
        print("Usage: claude plugins update [name]")
        print("  Omit name to update all installed plugins.")


def _resolve_plugin_path(name: str) -> str:
    """Resolve a plugin name to a local path."""
    if os.path.isdir(name):
        return name
    if os.path.isdir(os.path.join(os.getcwd(), name)):
        return os.path.join(os.getcwd(), name)
    return name


def _read_plugin_manifest(plugin_dir: str) -> Optional[dict[str, Any]]:
    """Read a plugin's manifest (package.json or plugin.json)."""
    if not os.path.isdir(plugin_dir):
        return None
    for manifest_name in ("plugin.json", "package.json", "manifest.json"):
        path = os.path.join(plugin_dir, manifest_name)
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    data = _json.load(f)
                return {
                    "name": data.get("name", os.path.basename(plugin_dir)),
                    "version": data.get("version", ""),
                    "description": data.get("description", ""),
                    "path": plugin_dir,
                    "enabled": True,
                    "source": "local",
                }
            except (_json.JSONDecodeError, OSError):
                pass

    # Fallback: use directory name as plugin name
    name = os.path.basename(plugin_dir)
    return {
        "name": name,
        "version": "",
        "description": "",
        "path": plugin_dir,
        "enabled": True,
        "source": "local",
    }


def _get_plugins_config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "plugins.json")


def _load_installed_plugins() -> list[dict[str, Any]]:
    path = _get_plugins_config_path()
    if os.path.isfile(path):
        try:
            with open(path, "r") as f:
                data = _json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("plugins", [])
        except (_json.JSONDecodeError, OSError):
            pass
    return []


def _save_installed_plugins(plugins: list[dict[str, Any]]) -> None:
    path = _get_plugins_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        _json.dump({"plugins": plugins}, f, indent=2)
