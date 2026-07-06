"""
Agent tool utilities — tool resolution and filtering.

Port of: src/tools/AgentTool/agentToolUtils.ts

Implements TS filterToolsForAgent with 3-layer filtering:
1. MCP tools always allowed
2. ALL_AGENT_DISALLOWED_TOOLS — always excluded
3. ASYNC_AGENT_ALLOWED_TOOLS — whitelist for background agents
"""

from __future__ import annotations

from typing import Any, Sequence

from hare.tools_impl.AgentTool.built_in_agents import AgentDefinition


def filter_tools_for_agent(
    tools: Sequence[dict[str, Any]],
    *,
    is_builtin: bool = True,
    is_async: bool = False,
    permission_mode: str = "",
) -> list[dict[str, Any]]:
    """Filter tools for sub-agent usage. TS filterToolsForAgent.

    Three-layer filtering:
    1. MCP tools (prefix 'mcp__') — always allowed
    2. ALL_AGENT_DISALLOWED_TOOLS — globally excluded (Agent, TaskOutput, etc.)
    3. Async agents — whitelist only (ASYNC_AGENT_ALLOWED_TOOLS)
    """
    from hare.tools import (
        ALL_AGENT_DISALLOWED_TOOLS,
        ASYNC_AGENT_ALLOWED_TOOLS,
        CUSTOM_AGENT_DISALLOWED_TOOLS,
    )

    disallowed_set = set(ALL_AGENT_DISALLOWED_TOOLS)
    custom_disallowed_set = set(CUSTOM_AGENT_DISALLOWED_TOOLS)
    async_allowed_set = set(ASYNC_AGENT_ALLOWED_TOOLS)

    result: list[dict[str, Any]] = []
    for tool in tools:
        name = (
            tool.get("name", "")
            if isinstance(tool, dict)
            else getattr(tool, "name", "")
        )

        # 1. MCP tools always allowed (TS: tool.name.startsWith('mcp__'))
        if isinstance(name, str) and name.startswith("mcp__"):
            result.append(tool)
            continue

        # 2. Global disallowed — always excluded
        if isinstance(name, str) and name in disallowed_set:
            continue

        # 2b. Custom agent additional disallowed
        if not is_builtin and isinstance(name, str) and name in custom_disallowed_set:
            continue

        # 3. Async agents — whitelist only
        if is_async and isinstance(name, str) and name not in async_allowed_set:
            continue

        result.append(tool)

    return result


def resolve_agent_tools(
    agent_definition: AgentDefinition,
    all_tools: Sequence[dict[str, Any]],
    *,
    is_async: bool = False,
    is_main_thread: bool = False,
) -> list[dict[str, Any]]:
    """Resolve the set of tools available to an agent.

    TS resolveAgentTools:
    1. Apply per-agent tool allow/deny lists
    2. Apply global filterToolsForAgent (unless main thread)
    3. Return resolved list
    """
    if is_main_thread:
        # Main thread: skip filterToolsForAgent — tools already assembled
        return list(all_tools)

    allowed = agent_definition.tools
    disallowed = set(agent_definition.disallowed_tools)

    # Per-agent tool allow/deny
    if allowed and "*" not in allowed:
        filtered: list[dict[str, Any]] = []
        allow_set = set(allowed)
        for t in all_tools:
            name = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
            if name in allow_set:
                filtered.append(t)
    else:
        filtered = list(all_tools)

    if disallowed:
        filtered = [
            t
            for t in filtered
            if (t.get("name", "") if isinstance(t, dict) else getattr(t, "name", ""))
            not in disallowed
        ]

    # Global filtering
    return filter_tools_for_agent(
        filtered,
        is_builtin=agent_definition.source == "built-in",
        is_async=is_async or agent_definition.background,
        permission_mode=agent_definition.permission_mode,
    )


def get_agent_model(
    agent_definition: AgentDefinition,
    *,
    parent_model: str = "",
    requested_model: str = "",
) -> str:
    """Determine which model an agent should use.

    TS getAgentModel: requested > agent definition model > parent (inherit).
    Special handling for 'inherit' and 'haiku' model strings.
    """
    if requested_model:
        return requested_model
    if agent_definition.model:
        if agent_definition.model == "inherit":
            return parent_model or "claude-sonnet-4-6"
        return agent_definition.model
    return parent_model


def format_agent_line(agent: AgentDefinition) -> str:
    """Format one agent line for display."""
    tools_desc = _get_tools_description(agent)
    bg = " [background]" if agent.background else ""
    return f"- {agent.agent_type}: {agent.when_to_use} (Tools: {tools_desc}){bg}"


def _get_tools_description(agent: AgentDefinition) -> str:
    """Get a description of the tools available to an agent."""
    has_allowlist = bool(agent.tools) and "*" not in agent.tools
    has_denylist = bool(agent.disallowed_tools)

    if has_allowlist and has_denylist:
        deny_set = set(agent.disallowed_tools)
        effective = [t for t in agent.tools if t not in deny_set]
        return ", ".join(effective) if effective else "None"
    elif has_allowlist:
        return ", ".join(agent.tools)
    elif has_denylist:
        return f"All except {', '.join(agent.disallowed_tools)}"
    return "All tools"
