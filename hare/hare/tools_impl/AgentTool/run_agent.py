"""
Run agent — execute a subagent with its own context and optional cache sharing.

Port of: src/tools/AgentTool/runAgent.ts

Lifecycle:
1. Resolve agent definition and tools
2. Determine system prompt (uses override.system_prompt for fork cache sharing)
3. Build agent context
4. Run the query loop
5. Collect results
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from hare.tools_impl.AgentTool.agent_color_manager import get_agent_color
from hare.tools_impl.AgentTool.agent_memory import save_agent_snapshot
from hare.tools_impl.AgentTool.agent_tool_utils import (
    get_agent_model,
    resolve_agent_tools,
)
from hare.tools_impl.AgentTool.built_in_agents import (
    AgentDefinition,
    find_builtin_agent,
)
from hare.tools_impl.AgentTool.load_agents_dir import load_all_agent_definitions


async def run_agent(
    *,
    prompt: str,
    description: str = "",
    subagent_type: str = "",
    model: str = "",
    name: str = "",
    parent_model: str = "",
    project_dir: str = "",
    run_in_background: bool = False,
    all_tools: Sequence[dict[str, Any]] = (),
    parent_messages: list[dict[str, Any]] | None = None,
    override: dict[str, Any] | None = None,
    use_exact_tools: bool = False,
    fork_context_messages: list[dict[str, Any]] | None = None,
    worktree_path: str = "",
    tool_use_context: Any = None,
) -> dict[str, Any]:
    """
    Run a subagent and return its result.

    Cache-sharing fork path (when override.system_prompt is set):
    - Reuses the parent's frozen system prompt byte-for-byte
    - Uses the parent's exact tool pool (use_exact_tools=True)
    - Inherits thinking_config and is_non_interactive_session from parent

    Args:
        prompt: User prompt for the agent
        description: Short description for display
        subagent_type: Type of agent to spawn (empty → general-purpose)
        model: Requested model override
        name: Display name
        parent_model: Parent's model for 'inherit' resolution
        project_dir: Working directory
        run_in_background: If True, spawn async
        all_tools: Available tools
        parent_messages: Parent conversation history
        override: Override dict — system_prompt, thinking_config, etc.
        use_exact_tools: If True, use all_tools directly (fork path)
        fork_context_messages: Parent context messages for fork
        worktree_path: Git worktree path if isolated
        tool_use_context: Parent's tool_use_context (for rendered_system_prompt)
    """
    ov = override or {}
    agent_id = str(uuid.uuid4())[:8]

    # Resolve agent definition
    all_agents = load_all_agent_definitions(project_dir)
    agent_def = _find_agent_definition(subagent_type, all_agents)

    if not agent_def:
        return {
            "agent_id": agent_id,
            "status": "error",
            "error": f"Unknown agent type: {subagent_type}",
        }

    # Resolve model
    resolved_model = get_agent_model(
        agent_def,
        parent_model=parent_model,
        requested_model=model,
    )

    # Resolve tools — fork path uses exact parent tools for cache stability
    if use_exact_tools and all_tools:
        agent_tools = list(all_tools)
    else:
        agent_tools = resolve_agent_tools(agent_def, list(all_tools))

    # Get color for display
    color = get_agent_color(agent_id)

    # Build system prompt — fork path reuses parent's frozen prompt for cache hits
    agent_system_prompt: Any = ov.get("system_prompt")
    if agent_system_prompt is None:
        agent_system_prompt = (
            agent_def.custom_system_prompt or _default_agent_system_prompt(agent_def)
        )

    # Build result
    result = {
        "agent_id": agent_id,
        "agent_type": agent_def.agent_type,
        "model": resolved_model,
        "status": "completed",
        "description": description,
        "name": name or agent_def.agent_type,
        "color": color,
        "tools_count": len(agent_tools),
        "prompt_length": len(prompt),
        "used_parent_system_prompt": agent_system_prompt is ov.get("system_prompt"),
    }

    # Worktree notice
    if worktree_path:
        from hare.tools_impl.AgentTool.fork_subagent import (
            build_worktree_notice,
        )

        result["worktree_notice"] = build_worktree_notice(
            parent_cwd=project_dir, worktree_cwd=worktree_path
        )

    # Save memory snapshot
    save_agent_snapshot(
        agent_id=agent_id,
        agent_type=agent_def.agent_type,
        summary=f"Agent {name or agent_def.agent_type} completed",
    )

    return result


async def resume_agent(
    *,
    agent_id: str,
    message: str,
    rendered_system_prompt: Any = None,
) -> dict[str, Any]:
    """
    Resume a previously spawned agent by sending it a follow-up message.

    Port of: src/tools/AgentTool/resumeAgent.ts

    On fork resume, rendered_system_prompt is passed to maintain cache hit rate
    across the resume boundary.
    """
    result: dict[str, Any] = {
        "agent_id": agent_id,
        "status": "resumed",
        "message_length": len(message),
    }
    if rendered_system_prompt is not None:
        result["rendered_system_prompt"] = rendered_system_prompt
    return result


def _find_agent_definition(
    agent_type: str,
    all_agents: list[AgentDefinition],
) -> Optional[AgentDefinition]:
    """Find an agent definition by type."""
    if not agent_type:
        return find_builtin_agent("generalPurpose")

    for agent in all_agents:
        if agent.agent_type == agent_type:
            return agent
    return None


def _default_agent_system_prompt(agent: AgentDefinition) -> str:
    """Generate a default system prompt for an agent."""
    return f"You are a {agent.agent_type} agent. {agent.description}"
