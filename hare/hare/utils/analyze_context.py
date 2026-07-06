"""
Context window breakdown for `/context` UI (port of `analyzeContext.ts`).

Full token accounting depends on compaction, tools, and API counters. This
module defines the public datatypes and entrypoints; wire services to match TS.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from hare.services.token_estimation import rough_token_count_estimation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_TOKEN_COUNT_OVERHEAD = 500
"""Fixed token overhead added by the API when tools are present."""

RESERVED_CATEGORY_NAME = "Autocompact buffer"
MANUAL_COMPACT_BUFFER_NAME = "Compact buffer"

# Per-message role/framing overhead estimate (mirrors TS MessageBreakdown logic)
_MSG_OVERHEAD = 4

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ContextCategory:
    name: str
    tokens: int
    color: str
    is_deferred: bool = False


@dataclass
class GridSquare:
    color: str
    is_filled: bool
    category_name: str
    tokens: int
    percentage: int
    square_fullness: float


@dataclass
class ContextData:
    categories: list[ContextCategory] = field(default_factory=list)
    total_tokens: int = 0
    max_tokens: int = 0
    raw_max_tokens: int = 0
    percentage: int = 0
    grid_rows: list[list[GridSquare]] = field(default_factory=list)
    model: str = ""
    memory_files: list[dict[str, Any]] = field(default_factory=list)
    mcp_tools: list[dict[str, Any]] = field(default_factory=list)
    deferred_builtin_tools: list[dict[str, Any]] | None = None
    system_tools: list[dict[str, Any]] | None = None
    system_prompt_sections: list[dict[str, Any]] | None = None
    agents: list[dict[str, Any]] = field(default_factory=list)
    slash_commands: dict[str, Any] | None = None
    skills: dict[str, Any] | None = None
    auto_compact_threshold: int | None = None
    is_auto_compact_enabled: bool = False
    message_breakdown: dict[str, Any] | None = None
    api_usage: dict[str, int] | None = None


# ====================================================================
# Token counting helpers
# ====================================================================


def _extract_section_name(content: str) -> str:
    """Extract a human-readable name from a system prompt section's content."""
    if not content:
        return "(empty)"
    # Try to find first markdown heading
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            # Remove leading # markers
            return stripped.lstrip("#").strip()
    # Fall back to a truncated preview of the first non-empty line
    first_line = next(
        (l for l in content.split("\n") if l.strip()), ""
    )
    if len(first_line) > 40:
        return first_line[:40] + "…"
    return first_line if first_line else "(empty)"


def _tool_to_signature(tool: Any) -> dict[str, Any]:
    """Build a serialisable signature for token counting.

    Returns a dict with ``name``, ``description``, and ``input_schema`` keys
    that can be JSON-serialised for the token estimation pipeline.
    """
    name = getattr(tool, "name", "unknown")
    desc = ""
    if hasattr(tool, "description"):
        try:
            d = tool.description
            if callable(d):
                desc = d({}, {}) if hasattr(d, "__code__") else str(d)
            else:
                desc = str(d)
        except Exception:
            desc = name
    elif hasattr(tool, "prompt"):
        try:
            p = tool.prompt
            if callable(p):
                desc = p({"tools": [], "agents": []}) if hasattr(p, "__code__") else str(p)
            else:
                desc = str(p)
        except Exception:
            desc = name
    else:
        desc = name

    schema: dict[str, Any] = {}
    if hasattr(tool, "input_schema"):
        try:
            s = tool.input_schema
            schema = s() if callable(s) else s
        except Exception:
            pass
    elif hasattr(tool, "inputJSONSchema"):
        schema = getattr(tool, "inputJSONSchema", {})

    return {"name": name, "description": desc, "input_schema": schema}


async def _count_tokens_via_api(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int | None:
    """Attempt to count tokens via the Anthropic count-tokens API.

    Falls back to rough estimation on any failure.
    """
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None

        client = anthropic.AsyncAnthropic(api_key=api_key)
        kwargs: dict[str, Any] = {
            "messages": messages,
            "model": "claude-sonnet-4-6-20260301",
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.beta.messages.count_tokens(**kwargs)
        return getattr(response, "input_tokens", None)
    except Exception:
        return None


async def _count_tokens_with_fallback(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int | None:
    """Count tokens via API, falling back to rough estimation.

    Returns *None* when the API is unavailable and rough estimation results
    in zero.
    """
    # Try the count-tokens API first
    api_result = await _count_tokens_via_api(messages, tools)
    if api_result is not None:
        return api_result

    # Fall back to rough estimation
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += rough_token_count_estimation(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += rough_token_count_estimation(json.dumps(block))
                else:
                    total += rough_token_count_estimation(str(block))
    if tools:
        for tool_def in tools:
            total += rough_token_count_estimation(json.dumps(tool_def))

    return total if total > 0 else None


def _get_context_window(model: str) -> int:
    """Resolve context window size for *model*, with safe fallback."""
    try:
        from hare.utils.context import get_context_window_for_model

        return get_context_window_for_model(model)
    except Exception:
        return 200_000


def _is_auto_compact_enabled() -> bool:
    """Check whether auto-compaction is enabled."""
    try:
        from hare.services.compact.compact_warning_hook import (
            get_effective_context_window_size,
        )

        _ = get_effective_context_window_size()
        return True
    except Exception:
        return False


def _get_auto_compact_threshold(model: str) -> int | None:
    """Return the auto-compact threshold for *model*, or *None*."""
    try:
        from hare.services.compact.compact_warning_hook import (
            AUTOCOMPACT_BUFFER_TOKENS,
            get_effective_context_window_size,
        )

        effective = get_effective_context_window_size(model)
        threshold = effective - AUTOCOMPACT_BUFFER_TOKENS
        return max(0, threshold)
    except Exception:
        return None


def _get_current_usage(messages: list[Any]) -> dict[str, int] | None:
    """Extract API token usage from the last response in *messages*."""
    if not messages:
        return None
    # Walk backwards to find the last message with usage data
    for msg in reversed(messages):
        m_attr = getattr(msg, "message", None) if hasattr(msg, "message") else None
        usage = None
        if m_attr is not None:
            usage = getattr(m_attr, "usage", None)
        if isinstance(usage, dict):
            return {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_creation_input_tokens": usage.get(
                    "cache_creation_input_tokens", 0
                ),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            }
    return None


# ====================================================================
# Category-specific token counters
# ====================================================================


async def _count_system_tokens(
    effective_system_prompt: list[str],
) -> tuple[int, list[dict[str, Any]]]:
    """Count tokens consumed by system prompt sections.

    Returns ``(total_tokens, section_details)``.
    """
    if not effective_system_prompt:
        return 0, []

    # Build named entries: each section gets a name extracted from its content
    named_entries: list[dict[str, Any]] = [
        {"name": _extract_section_name(content), "content": content}
        for content in effective_system_prompt
        if content and len(content.strip()) > 0
    ]

    if not named_entries:
        return 0, []

    # Count each section individually
    system_prompt_sections: list[dict[str, Any]] = []
    total_tokens = 0

    for entry in named_entries:
        tokens = 0
        msg: list[dict[str, Any]] = [{"role": "user", "content": entry["content"]}]
        result = await _count_tokens_with_fallback(msg, [])
        if result is not None:
            tokens = result
        else:
            tokens = rough_token_count_estimation(entry["content"])

        system_prompt_sections.append(
            {"name": entry["name"], "tokens": tokens}
        )
        total_tokens += tokens

    return total_tokens, system_prompt_sections


async def _count_memory_file_tokens() -> tuple[int, list[dict[str, Any]]]:
    """Count tokens consumed by CLAUDE.md / memory files.

    Returns ``(total_tokens, file_details)``.
    """
    # Simple mode disables CLAUDE.md loading
    if os.environ.get("CLAUDE_CODE_SIMPLE"):
        return 0, []

    try:
        from hare.utils.claudemd import get_memory_files
    except Exception:
        return 0, []

    try:
        memory_files_data = await get_memory_files()
    except Exception:
        return 0, []

    if not memory_files_data:
        return 0, []

    memory_file_details: list[dict[str, Any]] = []
    total_tokens = 0

    for file_info in memory_files_data:
        content = getattr(file_info, "content", "")
        path = getattr(file_info, "path", "unknown")
        file_type = getattr(file_info, "type", "unknown")

        tokens = 0
        result = await _count_tokens_with_fallback(
            [{"role": "user", "content": content}], []
        )
        if result is not None:
            tokens = result
        else:
            tokens = rough_token_count_estimation(content)

        total_tokens += tokens
        memory_file_details.append(
            {"path": path, "type": file_type, "tokens": tokens}
        )

    return total_tokens, memory_file_details


async def _count_custom_agent_tokens(
    agent_definitions: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Count tokens consumed by custom (non-builtin) agent definitions.

    Returns ``(total_tokens, agent_details)``.
    """
    if agent_definitions is None:
        return 0, []

    active_agents: list[Any] = []
    if hasattr(agent_definitions, "activeAgents"):
        active_agents = agent_definitions.activeAgents
    elif hasattr(agent_definitions, "active_agents"):
        active_agents = agent_definitions.active_agents
    elif isinstance(agent_definitions, list):
        active_agents = agent_definitions

    # Filter to non-builtin agents
    custom_agents = [
        a
        for a in active_agents
        if getattr(a, "source", "built-in") not in ("built-in", "builtin")
    ]

    if not custom_agents:
        return 0, []

    agent_details: list[dict[str, Any]] = []
    total_tokens = 0

    for agent in custom_agents:
        agent_type = getattr(agent, "agentType", getattr(agent, "agent_type", "unknown"))
        when_to_use = getattr(agent, "whenToUse", getattr(agent, "when_to_use", ""))
        source = getattr(agent, "source", "custom")
        content = f"{agent_type} {when_to_use}"

        tokens = 0
        result = await _count_tokens_with_fallback(
            [{"role": "user", "content": content}], []
        )
        if result is not None:
            tokens = result
        else:
            tokens = rough_token_count_estimation(content)

        total_tokens += tokens
        agent_details.append(
            {"agentType": agent_type, "source": source, "tokens": tokens}
        )

    return total_tokens, agent_details


async def _count_tool_definition_tokens_inner(
    tools: list[Any],
    model: str | None = None,
) -> int:
    """Count tokens consumed by a list of tool definitions via API or estimation.

    Each tool is converted to an API-schema-compatible dict, then the batch
    is counted in a single API call with overhead correction.
    """
    if not tools:
        return 0

    tool_schemas: list[dict[str, Any]] = []
    for tool in tools:
        sig = _tool_to_signature(tool)
        tool_schemas.append(
            {
                "name": sig["name"],
                "description": sig["description"],
                "input_schema": sig["input_schema"],
            }
        )

    result = await _count_tokens_with_fallback([], tool_schemas)
    if result is not None and result > 0:
        return max(0, result - TOOL_TOKEN_COUNT_OVERHEAD)

    # Local fallback: sum rough estimates
    total = 0
    for schema in tool_schemas:
        total += rough_token_count_estimation(json.dumps(schema))
    return total


async def _count_builtin_tool_tokens(
    tools: list[Any],
    model: str | None = None,
    messages: list[Any] | None = None,
) -> tuple[int, list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Count tokens for built-in (non-MCP) tools.

    Separates always-loaded tools from deferred tools when tool-search is
    active.

    Returns:
        ``(built_in_tool_tokens, deferred_details, deferred_tokens, system_tool_details)``
    """
    if not tools:
        return 0, [], 0, []

    builtin_tools = [
        t for t in tools if not getattr(t, "isMcp", False)
        and not getattr(t, "is_mcp", False)
        and not (getattr(t, "name", "").startswith("mcp__"))
    ]

    if not builtin_tools:
        return 0, [], 0, []

    # Check tool search / deferral state
    is_deferred = False
    try:
        from hare.services.tools.tool_search import is_tool_search_enabled

        is_deferred = await is_tool_search_enabled(
            model or "",
            tools,
            # Minimal permission context — real callers pass a lambda
            {},
            [],
            "analyzeBuiltIn",
        )
    except Exception:
        pass

    if not is_deferred:
        # All tools are loaded: count in one batch
        total = await _count_tool_definition_tokens_inner(builtin_tools, model)
        return total, [], 0, []

    # Tool search is enabled: separate always-loaded from deferred
    deferred_builtin_tools: list[Any] = []
    always_loaded: list[Any] = []

    try:
        from hare.services.tools.tool_search import is_deferred_tool

        for tool in builtin_tools:
            if is_deferred_tool(tool):
                deferred_builtin_tools.append(tool)
            else:
                always_loaded.append(tool)
    except Exception:
        # If we can't classify, treat all as always-loaded
        total = await _count_tool_definition_tokens_inner(builtin_tools, model)
        return total, [], 0, []

    # Count always-loaded tools
    always_loaded_tokens = (
        await _count_tool_definition_tokens_inner(always_loaded, model)
        if always_loaded
        else 0
    )

    # Build per-tool breakdown for always-loaded (system tools)
    system_tool_details: list[dict[str, Any]] = []
    if always_loaded and always_loaded_tokens > 0:
        estimates = []
        for t in always_loaded:
            sig = _tool_to_signature(t)
            estimates.append(
                rough_token_count_estimation(
                    json.dumps(sig.get("input_schema", {}))
                )
            )
        estimate_total = sum(estimates) or 1
        distributable = max(0, always_loaded_tokens - TOOL_TOKEN_COUNT_OVERHEAD)
        for i, t in enumerate(always_loaded):
            system_tool_details.append(
                {
                    "name": getattr(t, "name", "unknown"),
                    "tokens": max(
                        0,
                        round((estimates[i] / estimate_total) * distributable)
                        if i < len(estimates)
                        else 0,
                    ),
                }
            )
        # Sort by token count descending
        system_tool_details.sort(key=lambda d: d["tokens"], reverse=True)

    # Count deferred tools
    deferred_details: list[dict[str, Any]] = []
    loaded_deferred_tokens = 0
    total_deferred_tokens = 0

    if deferred_builtin_tools:
        # Find which deferred tools have been used in messages
        loaded_tool_names: set[str] = set()
        if messages:
            deferred_name_set = {getattr(t, "name", "") for t in deferred_builtin_tools}
            for msg in messages:
                msg_type = getattr(msg, "type", None)
                if msg_type == "assistant":
                    msg_content = getattr(getattr(msg, "message", None), "content", None)
                    if isinstance(msg_content, list):
                        for block in msg_content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                name = block.get("name", "")
                                if name in deferred_name_set:
                                    loaded_tool_names.add(name)

        for tool in deferred_builtin_tools:
            name = getattr(tool, "name", "unknown")
            tokens = await _count_tool_definition_tokens_inner([tool], model)
            tokens = max(0, tokens)

            is_loaded = name in loaded_tool_names
            deferred_details.append(
                {"name": name, "tokens": tokens, "isLoaded": is_loaded}
            )
            total_deferred_tokens += tokens
            if is_loaded:
                loaded_deferred_tokens += tokens

    # When deferred, only count always-loaded + loaded deferred
    built_in_tool_tokens = always_loaded_tokens + loaded_deferred_tokens
    unloaded_deferred = total_deferred_tokens - loaded_deferred_tokens

    return built_in_tool_tokens, deferred_details, unloaded_deferred, system_tool_details


async def _count_skill_definition_tokens(
    tools: list[Any],
) -> tuple[int, dict[str, Any]]:
    """Count tokens from skill definitions (frontmatter: name, description, whenToUse).

    Returns ``(skill_tokens, skill_info_dict)``.
    """
    try:
        from hare.services.skill_search import get_limited_skill_tool_commands
    except Exception:
        return 0, {"totalSkills": 0, "includedSkills": 0, "skillFrontmatter": []}

    cwd = os.getcwd()
    try:
        skills = await get_limited_skill_tool_commands(cwd)
    except Exception:
        return 0, {"totalSkills": 0, "includedSkills": 0, "skillFrontmatter": []}

    if not skills:
        return 0, {"totalSkills": 0, "includedSkills": 0, "skillFrontmatter": []}

    skill_frontmatter: list[dict[str, Any]] = []
    total_tokens = 0

    for skill in skills:
        name = getattr(skill, "name", "unknown")
        description = getattr(skill, "description", "")
        when_to_use = getattr(skill, "whenToUse", getattr(skill, "when_to_use", ""))
        source = getattr(skill, "source", "plugin")
        skill_type = getattr(skill, "type", "prompt")
        resolved_source = source if skill_type == "prompt" else "plugin"

        # Estimate frontmatter tokens (name + description + whenToUse)
        frontmatter_text = f"{name}: {description} {when_to_use}"
        tokens = rough_token_count_estimation(frontmatter_text)

        total_tokens += tokens
        skill_frontmatter.append(
            {"name": name, "source": resolved_source, "tokens": tokens}
        )

    return total_tokens, {
        "totalSkills": len(skills),
        "includedSkills": len(skills),
        "skillFrontmatter": skill_frontmatter,
    }


async def _approximate_message_tokens(
    messages: list[Any],
) -> tuple[int, dict[str, Any]]:
    """Estimate token usage for conversation messages with a detailed breakdown.

    Micro-compacts first, then processes each message block to categorise
    tokens into tool calls, tool results, attachments, and regular messages.

    Returns ``(total_tokens, breakdown_dict)``.
    """
    # Initialise breakdown
    breakdown: dict[str, Any] = {
        "totalTokens": 0,
        "toolCallTokens": 0,
        "toolResultTokens": 0,
        "attachmentTokens": 0,
        "assistantMessageTokens": 0,
        "userMessageTokens": 0,
        "toolCallsByType": {},
        "toolResultsByType": {},
        "attachmentsByType": {},
    }

    if not messages:
        return 0, breakdown

    # Micro-compact messages first (try to)
    compacted_messages = messages
    try:
        from hare.services.compact.micro_compact import microcompact_messages

        mc_result = await microcompact_messages(messages)
        if hasattr(mc_result, "messages"):
            compacted_messages = mc_result.messages
    except Exception:
        pass

    # Build tool_use_id -> tool_name map
    tool_id_to_name: dict[str, str] = {}
    for msg in compacted_messages:
        msg_type = getattr(msg, "type", None)
        if msg_type == "assistant":
            msg_content = getattr(getattr(msg, "message", None), "content", None)
            if isinstance(msg_content, list):
                for block in msg_content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_id = block.get("id", "")
                        tool_name = block.get("name", "unknown")
                        if tool_id:
                            tool_id_to_name[tool_id] = tool_name

    # Process each message
    tool_call_tokens = 0
    tool_result_tokens = 0
    attachment_tokens = 0
    assistant_tokens = 0
    user_tokens = 0
    tool_calls_by_type: dict[str, int] = {}
    tool_results_by_type: dict[str, int] = {}
    attachments_by_type: dict[str, int] = {}

    for msg in compacted_messages:
        msg_type = getattr(msg, "type", None)

        if msg_type == "attachment":
            attachment = getattr(msg, "attachment", {})
            if isinstance(attachment, dict):
                block_str = json.dumps(attachment)
                block_tokens = rough_token_count_estimation(block_str)
                attachment_tokens += block_tokens
                attach_type = attachment.get("type", "unknown")
                attachments_by_type[attach_type] = (
                    attachments_by_type.get(attach_type, 0) + block_tokens
                )
            continue

        msg_content = getattr(getattr(msg, "message", None), "content", None)

        if msg_type == "assistant":
            if isinstance(msg_content, list):
                for block in msg_content:
                    if not isinstance(block, dict):
                        continue
                    block_str = json.dumps(block)
                    block_tokens = rough_token_count_estimation(block_str)
                    if block.get("type") == "tool_use":
                        tool_call_tokens += block_tokens
                        name = block.get("name", "unknown")
                        tool_calls_by_type[name] = (
                            tool_calls_by_type.get(name, 0) + block_tokens
                        )
                    else:
                        assistant_tokens += block_tokens
            elif isinstance(msg_content, str):
                tokens = rough_token_count_estimation(msg_content)
                assistant_tokens += tokens

        elif msg_type == "user":
            if isinstance(msg_content, list):
                for block in msg_content:
                    if not isinstance(block, dict):
                        continue
                    block_str = json.dumps(block)
                    block_tokens = rough_token_count_estimation(block_str)
                    if block.get("type") == "tool_result":
                        tool_result_tokens += block_tokens
                        tool_use_id = block.get("tool_use_id", "")
                        tool_name = tool_id_to_name.get(tool_use_id, "unknown")
                        tool_results_by_type[tool_name] = (
                            tool_results_by_type.get(tool_name, 0) + block_tokens
                        )
                    else:
                        user_tokens += block_tokens
            elif isinstance(msg_content, str):
                tokens = rough_token_count_estimation(msg_content)
                user_tokens += tokens

    # Compute total (try API fallback for accuracy)
    total_tokens = 0
    try:
        from hare.utils.messages import normalize_messages_for_api

        normalized = normalize_messages_for_api(compacted_messages)
        api_inputs: list[dict[str, Any]] = []
        for nm in normalized:
            nm_type = getattr(nm, "type", "")
            nm_content = getattr(getattr(nm, "message", None), "content", None)
            if nm_type == "assistant":
                api_inputs.append({"role": "assistant", "content": nm_content})
            else:
                msg_attr = getattr(nm, "message", None)
                if msg_attr is not None:
                    api_inputs.append(
                        {
                            "role": getattr(msg_attr, "role", "user"),
                            "content": getattr(msg_attr, "content", ""),
                        }
                    )
                else:
                    api_inputs.append({"role": "user", "content": str(nm_content)})

        result = await _count_tokens_with_fallback(api_inputs, [])
        if result is not None and result > 0:
            total_tokens = result
    except Exception:
        pass

    if total_tokens <= 0:
        total_tokens = (
            tool_call_tokens
            + tool_result_tokens
            + attachment_tokens
            + assistant_tokens
            + user_tokens
        )

    # Build output arrays for the breakdown, sorted by token count
    tool_calls_list = sorted(
        [
            {"name": name, "callTokens": tokens, "resultTokens": tool_results_by_type.get(name, 0)}
            for name, tokens in tool_calls_by_type.items()
        ]
        + [
            {"name": name, "callTokens": 0, "resultTokens": tokens}
            for name, tokens in tool_results_by_type.items()
            if name not in tool_calls_by_type
        ],
        key=lambda x: x["callTokens"] + x["resultTokens"],
        reverse=True,
    )

    attachments_list = sorted(
        [{"name": name, "tokens": tokens} for name, tokens in attachments_by_type.items()],
        key=lambda x: x["tokens"],
        reverse=True,
    )

    breakdown["totalTokens"] = total_tokens
    breakdown["toolCallTokens"] = tool_call_tokens
    breakdown["toolResultTokens"] = tool_result_tokens
    breakdown["attachmentTokens"] = attachment_tokens
    breakdown["assistantMessageTokens"] = assistant_tokens
    breakdown["userMessageTokens"] = user_tokens
    breakdown["toolCallsByType"] = tool_calls_list
    breakdown["attachmentsByType"] = attachments_list

    return total_tokens, breakdown


# ====================================================================
# Grid construction helpers
# ====================================================================


def _build_grid(
    categories: list[ContextCategory],
    context_window: int,
    terminal_width: int | None,
) -> list[list[GridSquare]]:
    """Build the context-visualisation grid from category data.

    The grid mirrors the TS grid layout: system prompt first, then category
    squares, free space in the middle, and reserved/buffer at the end.
    """
    if context_window <= 0:
        return []

    # Determine grid dimensions based on context size and terminal width
    is_narrow = terminal_width is not None and terminal_width < 80
    if context_window >= 1_000_000:
        grid_width = 5 if is_narrow else 20
        grid_height = 10
    else:
        grid_width = 5 if is_narrow else 10
        grid_height = 5 if is_narrow else 10

    total_squares = grid_width * grid_height

    # Filter out deferred categories — they don't take up actual context space
    non_deferred = [c for c in categories if not c.is_deferred]

    # Calculate squares per category
    category_squares: list[dict[str, Any]] = []
    for cat in non_deferred:
        squares = max(1, round((cat.tokens / context_window) * total_squares))
        category_squares.append(
            {
                "name": cat.name,
                "color": cat.color,
                "tokens": cat.tokens,
                "squares": squares,
                "percentageOfTotal": round((cat.tokens / context_window) * 100),
                "isDeferred": cat.is_deferred,
            }
        )

    def _create_category_squares(cat_info: dict[str, Any]) -> list[GridSquare]:
        squares: list[GridSquare] = []
        exact = (cat_info["tokens"] / context_window) * total_squares
        whole = int(exact)
        fractional = exact - whole

        for i in range(cat_info["squares"]):
            fullness = 1.0
            if i == whole and fractional > 0:
                fullness = fractional
            squares.append(
                GridSquare(
                    color=cat_info["color"],
                    is_filled=True,
                    category_name=cat_info["name"],
                    tokens=cat_info["tokens"],
                    percentage=cat_info["percentageOfTotal"],
                    square_fullness=fullness,
                )
            )
        return squares

    # Separate reserved categories (autocompact / compact buffer)
    reserved_cat = next(
        (
            c
            for c in category_squares
            if c["name"] in (RESERVED_CATEGORY_NAME, MANUAL_COMPACT_BUFFER_NAME)
        ),
        None,
    )
    non_reserved = [
        c
        for c in category_squares
        if c["name"] not in (RESERVED_CATEGORY_NAME, MANUAL_COMPACT_BUFFER_NAME, "Free space")
    ]

    # Build grid squares in order
    grid_squares: list[GridSquare] = []

    # 1. Non-reserved, non-free categories first
    for cat in non_reserved:
        for square in _create_category_squares(cat):
            if len(grid_squares) < total_squares:
                grid_squares.append(square)

    # 2. Fill with free space, leaving room for reserved at end
    reserved_count = reserved_cat["squares"] if reserved_cat else 0
    free_target = total_squares - reserved_count
    free_cat = next((c for c in categories if c.name == "Free space"), None)
    free_tokens = free_cat.tokens if free_cat else 0
    free_pct = round((free_tokens / context_window) * 100) if context_window > 0 else 0

    while len(grid_squares) < free_target:
        grid_squares.append(
            GridSquare(
                color="promptBorder",
                is_filled=True,
                category_name="Free space",
                tokens=free_tokens,
                percentage=free_pct,
                square_fullness=1.0,
            )
        )

    # 3. Reserved squares at the end
    if reserved_cat:
        for square in _create_category_squares(reserved_cat):
            if len(grid_squares) < total_squares:
                grid_squares.append(square)

    # Convert to rows
    grid_rows: list[list[GridSquare]] = []
    for row_idx in range(grid_height):
        start = row_idx * grid_width
        row = grid_squares[start : start + grid_width]
        if row:
            grid_rows.append(row)

    return grid_rows


# ====================================================================
# Public API
# ====================================================================


async def count_tool_definition_tokens(
    tools: list[Any],
    get_tool_permission_context: Any,
    agent_info: Any | None,
    model: str | None = None,
) -> int:
    """Count the tokens consumed by the tool definitions in *tools*.

    Calls the Anthropic count-tokens API (beta endpoint) when available and
    falls back to rough local estimation.  Subtracts the fixed per-call
    ``TOOL_TOKEN_COUNT_OVERHEAD`` from the API result so callers that count
    multiple tool batches can sum them without double-counting the overhead.
    """
    if not tools:
        return 0

    try:
        return await _count_tool_definition_tokens_inner(tools, model)
    except Exception:
        logger.debug(
            "count_tool_definition_tokens: inner failed, using rough estimation"
        )
        total = 0
        for tool in tools:
            sig = _tool_to_signature(tool)
            total += rough_token_count_estimation(json.dumps(sig))
        return total


async def count_mcp_tool_tokens(
    tools: list[Any],
    get_tool_permission_context: Any,
    agent_info: Any | None,
    model: str | None = None,
) -> dict[str, Any]:
    """Count tokens consumed by MCP tool definitions.

    Returns a dict with:
      mcp_tool_tokens: Total token estimate for MCP tools currently loaded
      mcp_tool_details: List of {name, server_name, tokens, isLoaded} per MCP tool
      deferred_tool_tokens: Tokens for deferred (not yet loaded) MCP tools
      loaded_mcp_tool_names: Set of MCP tool names currently loaded

    When tool-search is active, only loaded MCP tools count toward usage;
    deferred tools are tracked separately for display.
    """
    mcp_tools = [
        t
        for t in tools
        if getattr(t, "name", "").startswith("mcp__")
        or getattr(t, "mcp_info", None) is not None
        or getattr(t, "isMcp", False)
        or getattr(t, "is_mcp", False)
    ]

    if not mcp_tools:
        return {"mcp_tool_tokens": 0, "mcp_tool_details": [], "deferred_tool_tokens": 0}

    # Single bulk API call for all MCP tools
    total_tokens_raw = await _count_tool_definition_tokens_inner(mcp_tools, model)
    total_tokens = max(0, total_tokens_raw)

    # Estimate per-tool proportions via local estimation
    estimates: list[int] = []
    for tool in mcp_tools:
        name = getattr(tool, "name", "unknown")
        desc = ""
        try:
            if hasattr(tool, "description"):
                d = tool.description
                desc = await d({}, {}) if callable(d) else str(d)
            elif hasattr(tool, "prompt"):
                p = tool.prompt
                desc = (
                    await p({"tools": [], "agents": []})
                    if callable(p)
                    else str(p)
                )
        except Exception:
            desc = name

        schema = {}
        if hasattr(tool, "input_schema"):
            try:
                s = tool.input_schema
                schema = s() if callable(s) else s
            except Exception:
                pass
        elif hasattr(tool, "inputJSONSchema"):
            schema = getattr(tool, "inputJSONSchema", {})

        sig_str = json.dumps({"name": name, "description": desc, "input_schema": schema})
        estimates.append(rough_token_count_estimation(sig_str))

    estimate_total = sum(estimates) or 1
    mcp_tokens_by_tool = [
        round((e / estimate_total) * total_tokens) for e in estimates
    ]

    # Check if MCP tools are deferred via tool search
    is_deferred = False
    try:
        from hare.services.tools.tool_search import is_tool_search_enabled

        is_deferred = await is_tool_search_enabled(
            model or "",
            tools,
            {},
            [],
            "analyzeMcp",
        )
    except Exception:
        pass

    # Build detail list
    mcp_tool_details: list[dict[str, Any]] = []
    loaded_mcp_tool_names: set[str] = set()

    for i, tool in enumerate(mcp_tools):
        name = getattr(tool, "name", "unknown")
        parts = name.split("__")
        server_name = parts[1] if len(parts) > 1 else "unknown"
        tokens = mcp_tokens_by_tool[i] if i < len(mcp_tokens_by_tool) else 0

        # All MCP tools are considered loaded when not deferred
        is_loaded = not is_deferred
        if is_loaded:
            loaded_mcp_tool_names.add(name)

        mcp_tool_details.append(
            {
                "name": name,
                "server_name": server_name,
                "tokens": tokens,
                "isLoaded": is_loaded,
            }
        )

    # Calculate loaded vs deferred
    loaded_tokens = 0
    deferred_tokens = 0
    for detail in mcp_tool_details:
        if detail["isLoaded"]:
            loaded_tokens += detail["tokens"]
        elif is_deferred:
            deferred_tokens += detail["tokens"]

    mcp_tool_tokens = loaded_tokens if is_deferred else total_tokens

    return {
        "mcp_tool_tokens": mcp_tool_tokens,
        "mcp_tool_details": mcp_tool_details,
        "deferred_tool_tokens": deferred_tokens if is_deferred else 0,
    }


async def analyze_context_usage(
    messages: list[Any],
    model: str,
    get_tool_permission_context: Any,
    tools: list[Any],
    agent_definitions: Any,
    terminal_width: int | None = None,
    tool_use_context: Any | None = None,
    main_thread_agent_definition: Any | None = None,
    original_messages: list[Any] | None = None,
) -> ContextData:
    """Analyse context-window usage and return a comprehensive breakdown.

    This is the main entrypoint for the ``/context`` command.  It gathers
    token counts from every source (system prompt, tools, memory files,
    agents, skills, messages) and returns a structured :class:`ContextData`
    including visualisation grid rows.

    Parameters
    ----------
    messages:
        The current conversation messages.
    model:
        Model name (may include ``[1m]`` suffix).
    get_tool_permission_context:
        Callable returning the current :class:`ToolPermissionContext`.
    tools:
        The full tool pool.
    agent_definitions:
        Active agent definitions.
    terminal_width:
        Terminal width in columns (affects grid density).
    tool_use_context:
        Per-turn tool-use context (carries custom system prompts, etc.).
    main_thread_agent_definition:
        The main-thread agent definition (affects system prompt).
    original_messages:
        Original messages before micro-compaction, used to extract API usage.

    Returns
    -------
    ContextData
        Full context-usage analysis.
    """
    # ------------------------------------------------------------------
    # Resolve runtime model and context window
    # ------------------------------------------------------------------
    runtime_model = model
    try:
        from hare.utils.model.model import get_runtime_main_loop_model

        runtime_model = get_runtime_main_loop_model(model)
    except Exception:
        pass

    context_window = _get_context_window(runtime_model)

    # ------------------------------------------------------------------
    # Build effective system prompt
    # ------------------------------------------------------------------
    effective_system_prompt: list[str] = []
    try:
        from hare.constants.prompts import get_system_prompt

        default_sp = await get_system_prompt(tools, runtime_model)
        if isinstance(default_sp, list):
            effective_system_prompt = list(default_sp)
        elif isinstance(default_sp, str):
            effective_system_prompt = [default_sp]

        # Apply custom / append system prompt from tool_use_context if present
        custom_sp = None
        append_sp = None
        if tool_use_context is not None:
            options = getattr(tool_use_context, "options", None)
            if options is not None:
                custom_sp = getattr(options, "custom_system_prompt", None)
                append_sp = getattr(options, "append_system_prompt", None)

        if main_thread_agent_definition is not None:
            gsp = getattr(main_thread_agent_definition, "get_system_prompt", None)
            if callable(gsp):
                try:
                    agent_sp = gsp()
                except Exception:
                    agent_sp = None
                if agent_sp:
                    effective_system_prompt = [agent_sp]
        elif isinstance(custom_sp, str) and custom_sp.strip():
            effective_system_prompt = [custom_sp]

        if isinstance(append_sp, str) and append_sp.strip():
            effective_system_prompt.append(append_sp)
    except Exception:
        logger.debug("analyze_context_usage: failed to build system prompt")

    # ------------------------------------------------------------------
    # Count tokens from all sources (with error isolation per section)
    # ------------------------------------------------------------------
    # System prompt
    try:
        system_tokens, system_sections = await _count_system_tokens(
            effective_system_prompt
        )
    except Exception:
        logger.debug("analyze_context_usage: system token count failed", exc_info=True)
        system_tokens = 0
        system_sections = []

    # Memory files
    try:
        claude_md_tokens, memory_file_details = await _count_memory_file_tokens()
    except Exception:
        logger.debug("analyze_context_usage: memory file count failed", exc_info=True)
        claude_md_tokens = 0
        memory_file_details = []

    # Built-in tools
    try:
        (
            builtin_tool_tokens,
            deferred_builtin_details,
            deferred_builtin_tokens,
            system_tool_details,
        ) = await _count_builtin_tool_tokens(
            tools, runtime_model, messages
        )
    except Exception:
        logger.debug("analyze_context_usage: builtin tool count failed", exc_info=True)
        builtin_tool_tokens = 0
        deferred_builtin_details = []
        deferred_builtin_tokens = 0
        system_tool_details = []

    # MCP tools
    try:
        mcp_result = await count_mcp_tool_tokens(
            tools, get_tool_permission_context, agent_definitions, runtime_model
        )
        mcp_tool_tokens = mcp_result.get("mcp_tool_tokens", 0)
        mcp_tool_details = mcp_result.get("mcp_tool_details", [])
        deferred_mcp_tokens = mcp_result.get("deferred_tool_tokens", 0)
    except Exception:
        logger.debug("analyze_context_usage: MCP tool count failed", exc_info=True)
        mcp_tool_tokens = 0
        mcp_tool_details = []
        deferred_mcp_tokens = 0

    # Custom agents
    try:
        agent_tokens, agent_details = await _count_custom_agent_tokens(agent_definitions)
    except Exception:
        logger.debug("analyze_context_usage: agent token count failed", exc_info=True)
        agent_tokens = 0
        agent_details = []

    # Skills
    try:
        skill_tokens, skill_info = await _count_skill_definition_tokens(tools)
    except Exception:
        logger.debug("analyze_context_usage: skill token count failed", exc_info=True)
        skill_tokens = 0
        skill_info = {"totalSkills": 0, "includedSkills": 0, "skillFrontmatter": []}

    # Calculate system tools tokens (builtin minus skills)
    system_tools_tokens = max(0, builtin_tool_tokens - skill_tokens)

    # Messages
    try:
        message_tokens, message_breakdown_raw = await _approximate_message_tokens(
            messages
        )
    except Exception:
        logger.debug("analyze_context_usage: message token count failed", exc_info=True)
        message_tokens = 0
        message_breakdown_raw = {}

    # ------------------------------------------------------------------
    # Auto-compact settings
    # ------------------------------------------------------------------
    is_auto_compact = _is_auto_compact_enabled()
    auto_compact_threshold = _get_auto_compact_threshold(runtime_model) if is_auto_compact else None

    # ------------------------------------------------------------------
    # Build categories in display order (mirrors TS)
    # ------------------------------------------------------------------
    cats: list[ContextCategory] = []

    # 1. System prompt (fixed overhead)
    if system_tokens > 0:
        cats.append(
            ContextCategory(
                name="System prompt",
                tokens=system_tokens,
                color="promptBorder",
            )
        )

    # 2. System tools (builtin minus skill tokens)
    if system_tools_tokens > 0:
        is_ant = os.environ.get("USER_TYPE") == "ant"
        cats.append(
            ContextCategory(
                name="[ANT-ONLY] System tools" if is_ant else "System tools",
                tokens=system_tools_tokens,
                color="inactive",
            )
        )

    # 3. MCP tools (loaded)
    if mcp_tool_tokens > 0:
        cats.append(
            ContextCategory(
                name="MCP tools",
                tokens=mcp_tool_tokens,
                color="cyan_FOR_SUBAGENTS_ONLY",
            )
        )

    # 4. Deferred MCP tools
    if deferred_mcp_tokens > 0:
        cats.append(
            ContextCategory(
                name="MCP tools (deferred)",
                tokens=deferred_mcp_tokens,
                color="inactive",
                is_deferred=True,
            )
        )

    # 5. Deferred builtin tools
    if deferred_builtin_tokens > 0:
        cats.append(
            ContextCategory(
                name="System tools (deferred)",
                tokens=deferred_builtin_tokens,
                color="inactive",
                is_deferred=True,
            )
        )

    # 6. Custom agents
    if agent_tokens > 0:
        cats.append(
            ContextCategory(
                name="Custom agents",
                tokens=agent_tokens,
                color="permission",
            )
        )

    # 7. Memory files
    if claude_md_tokens > 0:
        cats.append(
            ContextCategory(
                name="Memory files",
                tokens=claude_md_tokens,
                color="claude",
            )
        )

    # 8. Skills
    if skill_tokens > 0:
        cats.append(
            ContextCategory(
                name="Skills",
                tokens=skill_tokens,
                color="warning",
            )
        )

    # 9. Messages
    if message_tokens > 0:
        cats.append(
            ContextCategory(
                name="Messages",
                tokens=message_tokens,
                color="purple_FOR_SUBAGENTS_ONLY",
            )
        )

    # ------------------------------------------------------------------
    # Calculate actual usage and reserved space
    # ------------------------------------------------------------------
    # Actual usage excludes deferred categories
    actual_usage = sum(c.tokens for c in cats if not c.is_deferred)

    # Reserved space (auto-compact buffer or manual compact buffer)
    reserved_tokens = 0
    if is_auto_compact and auto_compact_threshold is not None:
        reserved_tokens = context_window - auto_compact_threshold
        cats.append(
            ContextCategory(
                name=RESERVED_CATEGORY_NAME,
                tokens=reserved_tokens,
                color="inactive",
            )
        )
    elif not is_auto_compact:
        try:
            from hare.services.compact.compact_warning_hook import (
                MANUAL_COMPACT_BUFFER_TOKENS,
            )

            reserved_tokens = MANUAL_COMPACT_BUFFER_TOKENS
        except Exception:
            reserved_tokens = 3000
        cats.append(
            ContextCategory(
                name=MANUAL_COMPACT_BUFFER_NAME,
                tokens=reserved_tokens,
                color="inactive",
            )
        )

    # Free space
    free_tokens = max(0, context_window - actual_usage - reserved_tokens)
    cats.append(
        ContextCategory(
            name="Free space",
            tokens=free_tokens,
            color="promptBorder",
        )
    )

    # ------------------------------------------------------------------
    # Compute final total (prefer API usage when available)
    # ------------------------------------------------------------------
    total_including_reserved = actual_usage

    api_usage = _get_current_usage(original_messages if original_messages else messages)
    total_from_api: int | None = None
    if api_usage:
        total_from_api = (
            api_usage.get("input_tokens", 0)
            + api_usage.get("cache_creation_input_tokens", 0)
            + api_usage.get("cache_read_input_tokens", 0)
        )

    final_total = total_from_api if total_from_api is not None else total_including_reserved
    pct = round((final_total / context_window) * 100) if context_window > 0 else 0

    # ------------------------------------------------------------------
    # Build the visualisation grid
    # ------------------------------------------------------------------
    grid_rows = _build_grid(cats, context_window, terminal_width)

    # ------------------------------------------------------------------
    # Format message breakdown
    # ------------------------------------------------------------------
    formatted_breakdown: dict[str, Any] | None = None
    if message_breakdown_raw:
        try:
            formatted_breakdown = {
                "toolCallTokens": message_breakdown_raw.get("toolCallTokens", 0),
                "toolResultTokens": message_breakdown_raw.get("toolResultTokens", 0),
                "attachmentTokens": message_breakdown_raw.get("attachmentTokens", 0),
                "assistantMessageTokens": message_breakdown_raw.get(
                    "assistantMessageTokens", 0
                ),
                "userMessageTokens": message_breakdown_raw.get("userMessageTokens", 0),
                "toolCallsByType": message_breakdown_raw.get("toolCallsByType", []),
                "attachmentsByType": message_breakdown_raw.get("attachmentsByType", []),
            }
        except Exception:
            logger.debug("analyze_context_usage: failed to format breakdown")

    # ------------------------------------------------------------------
    # Build result
    # ------------------------------------------------------------------
    return ContextData(
        categories=cats,
        total_tokens=final_total,
        max_tokens=context_window,
        raw_max_tokens=context_window,
        percentage=pct,
        grid_rows=grid_rows,
        model=runtime_model,
        memory_files=memory_file_details,
        mcp_tools=mcp_tool_details,
        deferred_builtin_tools=deferred_builtin_details if os.environ.get("USER_TYPE") == "ant" else None,
        system_tools=system_tool_details if os.environ.get("USER_TYPE") == "ant" else None,
        system_prompt_sections=system_sections if os.environ.get("USER_TYPE") == "ant" else None,
        agents=agent_details,
        slash_commands=None,  # stub — wire to countSlashCommandTokens when slash-command infra is available
        skills=(
            skill_info
            if skill_tokens > 0 and skill_info.get("totalSkills", 0) > 0
            else None
        ),
        auto_compact_threshold=auto_compact_threshold,
        is_auto_compact_enabled=is_auto_compact,
        message_breakdown=formatted_breakdown,
        api_usage=api_usage,
    )
