"""
Build `system/init` SDK message for session initialization.

Port of: src/utils/messages/systemInit.ts

Constructs the initial system message sent to SDK consumers when
a new session starts, including tools, model, MCP servers, and
permission mode information.
"""

from __future__ import annotations

from typing import Any, Optional

from hare.utils.cwd import get_cwd


def sdk_compat_tool_name(name: str) -> str:
    """Map Agent tool wire name for legacy SDK consumers."""
    if name == "Agent":
        return "Task"
    return name


def build_system_init_message(
    inputs: dict[str, Any],
    *,
    session_id: str = "",
    model: str = "",
    permission_mode: str = "default",
    fast_mode: bool = False,
    agents: Optional[list[dict[str, Any]]] = None,
    skills: Optional[list[dict[str, Any]]] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Assemble the system/init payload sent to SDK consumers on session start.

    This message informs the SDK client about the session's capabilities,
    available tools, MCP server status, and active plugins/skills.
    """
    # Build tools list with SDK-compatible names
    tools_data = inputs.get("tools", [])
    if isinstance(tools_data, list):
        tools = [
            {"name": sdk_compat_tool_name(t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")),
             "description": t.get("description", "") if isinstance(t, dict) else getattr(t, "description", "")}
            for t in tools_data
        ]
    else:
        tools = []

    # Build MCP server list with status
    mcp_clients = inputs.get("mcpClients", inputs.get("mcp_clients", []))
    mcp_servers = []
    for c in mcp_clients:
        if isinstance(c, dict):
            mcp_servers.append({
                "name": c.get("name", ""),
                "status": c.get("status", c.get("type", "disconnected")),
            })
        elif hasattr(c, "name"):
            mcp_servers.append({
                "name": c.name,
                "status": getattr(c, "status", getattr(c, "type", "disconnected")),
            })

    # Build agent listing
    agent_list = []
    if agents:
        for a in agents:
            if isinstance(a, dict):
                agent_list.append({"name": a.get("name", ""), "description": a.get("description", "")})
            elif hasattr(a, "name"):
                agent_list.append({"name": a.name, "description": getattr(a, "description", "")})

    # Build skills listing
    skill_list = []
    if skills:
        for s in skills:
            if isinstance(s, dict):
                skill_list.append({"name": s.get("name", ""), "description": s.get("description", "")})
            elif hasattr(s, "name"):
                skill_list.append({"name": s.name, "description": getattr(s, "description", "")})

    # Build plugins listing
    plugin_list = []
    if plugins:
        for p in plugins:
            if isinstance(p, dict):
                plugin_list.append({"name": p.get("name", ""), "version": p.get("version", "")})
            elif hasattr(p, "name"):
                plugin_list.append({"name": p.name, "version": getattr(p, "version", "")})

    return {
        "type": "system",
        "subtype": "init",
        "session_id": session_id,
        "cwd": get_cwd(),
        "model": model or inputs.get("model", ""),
        "permissionMode": permission_mode or inputs.get("permissionMode", "default"),
        "fastMode": fast_mode,
        "tools": tools,
        "mcp_servers": mcp_servers,
        "agents": agent_list,
        "skills": skill_list,
        "plugins": plugin_list,
    }
