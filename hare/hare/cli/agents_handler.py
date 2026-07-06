"""
CLI handler for agents subcommand — list, resolve overrides, group by source.

Port of: src/cli/handlers/agents.ts
"""

from __future__ import annotations

from typing import Any

AGENT_SOURCE_GROUPS = [
    ("projectSettings", "Project agents"),
    ("localSettings", "Local agents"),
    ("userSettings", "User agents"),
    ("plugin", "Plugin agents"),
    ("builtin", "Built-in agents"),
]


async def handle_agents_command(args: dict[str, Any]) -> None:
    """Handle the 'agents' CLI subcommand. Lists available agents with source info."""
    import os

    project_dir = args.get("project_dir", os.getcwd())
    json_output = args.get("json", False)

    try:
        agents = _load_agent_definitions(project_dir)
    except Exception as e:
        print(f"Failed to load agent definitions: {e}")
        return

    if not agents:
        print("No agents found.")
        return

    # Resolve overrides
    resolved = _resolve_agent_overrides(agents)

    if json_output:
        import json as _json

        print(_json.dumps([_agent_to_dict(a) for a in resolved], indent=2))
        return

    # Group by source
    by_source: dict[str, list[dict[str, Any]]] = {}
    for agent in resolved:
        source = agent.get("source", "builtin")
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(agent)

    # Count active agents (not shadowed)
    active = [a for a in resolved if not a.get("shadowed")]
    print(f"Available agents ({len(active)} active):")
    print()

    for source, label in AGENT_SOURCE_GROUPS:
        group = by_source.get(source, [])
        if not group:
            continue
        group.sort(key=lambda a: a.get("name", ""))
        for agent in group:
            _print_agent(agent)


def _load_agent_definitions(project_dir: str) -> list[dict[str, Any]]:
    """Load agents from filesystem and settings."""
    agents: list[dict[str, Any]] = []

    # Built-in agents
    builtin_dir = os.path.join(
        os.path.dirname(__file__), "..", "tools_impl", "AgentTool", "builtin"
    )
    if os.path.isdir(builtin_dir):
        for fname in sorted(os.listdir(builtin_dir)):
            if fname.endswith(".md") or fname.endswith(".json"):
                name = fname.rsplit(".", 1)[0]
                agents.append(
                    {
                        "name": name,
                        "source": "builtin",
                        "path": os.path.join(builtin_dir, fname),
                        "agent_type": _infer_agent_type(name),
                        "model": None,
                    }
                )

    # User project agents (CLAUDE.md / .claude/agents/)
    user_agents_dir = os.path.join(project_dir, ".claude", "agents")
    if os.path.isdir(user_agents_dir):
        for fname in sorted(os.listdir(user_agents_dir)):
            if fname.endswith(".md"):
                name = fname.rsplit(".", 1)[0]
                agents.append(
                    {
                        "name": name,
                        "source": "projectSettings",
                        "path": os.path.join(user_agents_dir, fname),
                        "agent_type": _infer_agent_type(name),
                        "model": None,
                    }
                )

    return agents


def _resolve_agent_overrides(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve agent overrides: later sources shadow earlier ones.

    Priority (lowest to highest): builtin < plugin < project < local < user
    """
    source_priority = {
        "builtin": 0,
        "plugin": 1,
        "projectSettings": 2,
        "localSettings": 3,
        "userSettings": 4,
    }
    seen: dict[str, dict[str, Any]] = {}

    for agent in sorted(
        agents, key=lambda a: source_priority.get(str(a.get("source", "")), 0)
    ):
        name = agent.get("name", "")
        if name in seen:
            seen[name]["shadowed"] = True
            seen[name]["shadowed_by"] = agent.get("source", "")
        seen[name] = dict(agent)
        seen[name]["shadowed"] = False

    result = []
    for agent in agents:
        name = agent.get("name", "")
        resolved = seen.get(name, agent)
        result.append(resolved)

    # Deduplicate by name, keeping last occurrence
    deduped: dict[str, dict[str, Any]] = {}
    for a in result:
        deduped[a.get("name", "")] = a

    return sorted(
        deduped.values(), key=lambda a: source_priority.get(str(a.get("source", "")), 0)
    )


def _infer_agent_type(name: str) -> str:
    """Infer agent type from name."""
    name_lower = name.lower()
    if "review" in name_lower:
        return "reviewer"
    if "test" in name_lower:
        return "tester"
    if "build" in name_lower or "ci" in name_lower:
        return "builder"
    return "general"


def _print_agent(agent: dict[str, Any]) -> None:
    """Print a single agent line with source and shadowed info."""
    name = agent.get("name", "unknown")
    agent_type = agent.get("agent_type", "")
    source = agent.get("source", "builtin")
    model = agent.get("model", "")
    shadowed = agent.get("shadowed")
    shadowed_by = agent.get("shadowed_by", "")

    # Source label
    source_labels = dict(AGENT_SOURCE_GROUPS)

    line = f"  {name}"
    if agent_type:
        line += f" ({agent_type})"
    if model:
        line += f" [model: {model}]"
    line += f"  — {source_labels.get(source, source)}"

    if shadowed:
        line += f" (shadowed by {shadowed_by})"

    print(line)


def _agent_to_dict(agent: dict[str, Any]) -> dict[str, Any]:
    """Convert agent to JSON-serializable dict."""
    return {
        "name": agent.get("name", ""),
        "source": agent.get("source", ""),
        "agent_type": agent.get("agent_type", ""),
        "model": agent.get("model"),
        "shadowed": agent.get("shadowed", False),
    }


import os
