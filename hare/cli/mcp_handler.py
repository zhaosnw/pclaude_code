"""
CLI handler for MCP subcommand — list, add, remove, get, serve.

Port of: src/cli/handlers/mcp.tsx
"""

from __future__ import annotations

import json as _json
from typing import Any


async def handle_mcp_command(args: dict[str, Any]) -> None:
    """Handle the 'mcp' CLI subcommand.

    Actions: list, add, remove, get, serve
    """
    action = args.get("action", "list")

    if action == "list":
        await _mcp_list(args)
    elif action == "add":
        await _mcp_add(args)
    elif action == "remove":
        await _mcp_remove(args)
    elif action == "get":
        await _mcp_get(args)
    elif action == "serve":
        await _mcp_serve(args)
    else:
        print(f"Unknown MCP action: {action}")
        print("Available: list, add, remove, get, serve")


async def _mcp_list(args: dict[str, Any]) -> None:
    """List configured MCP servers."""
    json_output = args.get("json", False)

    servers = _load_mcp_servers()

    if json_output:
        print(_json.dumps({"servers": servers}, indent=2))
        return

    if not servers:
        print("No MCP servers configured.")
        print("Add one with: claude mcp add <name> <command> [args...]")
        return

    print("Configured MCP servers:")
    print()
    for name, cfg in sorted(servers.items()):
        transport = cfg.get("transport", cfg.get("type", "stdio"))
        command = cfg.get(
            "command", cfg.get("args", [""])[0] if cfg.get("args") else ""
        )
        status = _check_server_health(name, cfg)
        status_icon = "✓" if status else "✗"
        print(f"  {status_icon} {name}")
        print(f"    Transport: {transport}")
        if command:
            print(f"    Command: {command}")
        print()


async def _mcp_add(args: dict[str, Any]) -> None:
    """Add an MCP server."""
    name = args.get("name", "")
    if not name:
        print("Usage: claude mcp add <name> <command> [args...]")
        print("")
        print("Examples:")
        print("  claude mcp add my-server -- python my_server.py")
        print(
            "  claude mcp add filesystem --transport stdio -- npx @anthropic/mcp-filesystem /path"
        )
        return

    command_args = args.get("args", args.get("command", []))
    if isinstance(command_args, str):
        command_args = command_args.split()

    transport = args.get("transport", "stdio")

    servers = _load_mcp_servers()
    servers[name] = {
        "transport": transport,
        "command": command_args[0] if command_args else "",
        "args": command_args,
    }

    _save_mcp_servers(servers)
    print(f"MCP server '{name}' added.")


async def _mcp_remove(args: dict[str, Any]) -> None:
    """Remove an MCP server by name."""
    name = args.get("name", "")
    if not name:
        print("Usage: claude mcp remove <name>")
        return

    servers = _load_mcp_servers()
    if name not in servers:
        print(f"MCP server '{name}' not found.")
        return

    # Check scopes: user, project, local
    scope = args.get("scope")
    if scope:
        if f"{scope}:{name}" in servers:
            del servers[f"{scope}:{name}"]
        else:
            del servers[name]
    else:
        del servers[name]

    _save_mcp_servers(servers)
    print(f"MCP server '{name}' removed.")


async def _mcp_get(args: dict[str, Any]) -> None:
    """Get details of a specific MCP server."""
    name = args.get("name", "")
    if not name:
        print("Usage: claude mcp get <name>")
        return

    servers = _load_mcp_servers()
    cfg = servers.get(name)
    if not cfg:
        print(f"MCP server '{name}' not found.")
        return

    json_output = args.get("json", False)
    if json_output:
        print(_json.dumps({name: cfg}, indent=2))
        return

    print(f"MCP server: {name}")
    for k, v in cfg.items():
        print(f"  {k}: {v}")


async def _mcp_serve(args: dict[str, Any]) -> None:
    """Serve an MCP server (SDK mode)."""
    print("MCP serve: Starts an MCP server in SDK mode.")
    print("Use 'claude mcp serve' with a configured MCP server name.")


def _load_mcp_servers() -> dict[str, Any]:
    """Load MCP server config from settings files."""
    import os

    servers: dict[str, Any] = {}
    config_paths = [
        os.path.join(os.path.expanduser("~"), ".claude", "mcp.json"),
        os.path.join(os.getcwd(), ".claude", "mcp.json"),
    ]

    for path in config_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = _json.load(f)
                if isinstance(data, dict):
                    mcp_servers = data.get("mcpServers", data.get("servers", {}))
                    if isinstance(mcp_servers, dict):
                        servers.update(mcp_servers)
            except (_json.JSONDecodeError, OSError):
                pass

    return servers


def _save_mcp_servers(servers: dict[str, Any]) -> None:
    """Save MCP server config."""
    import os

    config_dir = os.path.join(os.path.expanduser("~"), ".claude")
    os.makedirs(config_dir, exist_ok=True)

    mcp_file = os.path.join(config_dir, "mcp.json")
    existing: dict[str, Any] = {}
    if os.path.exists(mcp_file):
        try:
            with open(mcp_file, "r") as f:
                existing = _json.load(f)
        except (_json.JSONDecodeError, OSError):
            pass

    existing["mcpServers"] = servers
    with open(mcp_file, "w") as f:
        _json.dump(existing, f, indent=2)


def _check_server_health(name: str, cfg: dict[str, Any]) -> bool:
    """Check if an MCP server is healthy."""
    return True  # Stub: real health check requires subprocess probe
