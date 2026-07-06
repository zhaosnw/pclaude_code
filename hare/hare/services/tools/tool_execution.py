"""
Tool execution orchestration.

Port of: src/services/tools/toolExecution.ts (1745 lines TS — partial port).

Implements the core tool execution loop: lookup → validate → permission check
→ tool.call() → result mapping → MessageUpdateLazy yield.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Optional, Union

from hare.utils.messages import create_user_message
from hare.tool import find_tool_by_name

McpServerType = Optional[str]


@dataclass
class _ContextModifier:
    tool_use_id: str
    modify_context: Callable[[Any], Any]


@dataclass
class MessageUpdateLazy:
    """Message update emitted during tool execution.

    Mirrors TS MessageUpdateLazy: carries an optional message (usually
    a UserMessage with tool_result content) and an optional context_modifier
    that mutates the ToolUseContext between tools.
    """

    message: Optional[Any] = None
    context_modifier: Optional[_ContextModifier] = None


async def run_tool_use(
    tool_use: Any,
    assistant_message: Any,
    can_use_tool: Any,
    tool_use_context: Any,
) -> AsyncGenerator[MessageUpdateLazy, None]:
    """Run a single tool use: validate, permission-check, call, wrap result.

    Args:
        tool_use: dict with 'name', 'id', 'input' keys (ToolUseBlock shape)
        assistant_message: the AssistantMessage that contained this tool_use
        can_use_tool: async callable for permission checks
        tool_use_context: per-turn ToolUseContext

    Yields:
        MessageUpdateLazy with tool result messages and context modifiers
    """
    tool_name = (
        tool_use.get("name", "")
        if isinstance(tool_use, dict)
        else getattr(tool_use, "name", "")
    )
    tool_use_id = (
        tool_use.get("id", "")
        if isinstance(tool_use, dict)
        else getattr(tool_use, "id", "")
    )
    tool_input = (
        tool_use.get("input", {})
        if isinstance(tool_use, dict)
        else getattr(tool_use, "input", {})
    )

    if isinstance(tool_input, str):
        tool_input = {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Get tools list from context
    tools: list[Any] = []
    if hasattr(tool_use_context, "options"):
        tools = getattr(tool_use_context.options, "tools", [])

    # Lookup tool by name
    tool = find_tool_by_name(tools, tool_name) if tools else None

    # Unknown tool guard
    if tool is None:
        yield MessageUpdateLazy(
            message=create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"<tool_use_error>Tool '{tool_name}' not found</tool_use_error>",
                        "is_error": True,
                    }
                ],
                tool_use_result=f"Tool '{tool_name}' not found",
                source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
            )
        )
        return

    # Abort check
    abort_signal = _get_abort_signal(tool_use_context)
    if abort_signal is not None and getattr(abort_signal, "aborted", False):
        yield MessageUpdateLazy(
            message=create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "Tool execution cancelled (user aborted).",
                        "is_error": True,
                    }
                ],
                tool_use_result="User aborted",
                source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
            )
        )
        return

    # Permission check
    try:
        if can_use_tool is not None:
            permission = await can_use_tool(
                tool,
                tool_input,
                tool_use_context,
                assistant_message,
                tool_use_id,
                None,
            )
            if getattr(permission, "behavior", "allow") == "deny":
                reason = getattr(permission, "message", "Permission denied")
                yield MessageUpdateLazy(
                    message=create_user_message(
                        content=[
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": reason,
                                "is_error": True,
                            }
                        ],
                        tool_use_result=reason,
                        source_tool_assistant_uuid=getattr(
                            assistant_message, "uuid", None
                        ),
                    )
                )
                return
    except Exception as perm_err:
        yield MessageUpdateLazy(
            message=create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"Permission check failed: {perm_err}",
                        "is_error": True,
                    }
                ],
                tool_use_result=f"Permission error: {perm_err}",
                source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
            )
        )
        return

    # Execute tool call
    start_time = time.time()
    try:
        tool_result = await tool.call(
            tool_input,
            tool_use_context,
            can_use_tool,
            assistant_message,
        )

        # Build tool_result content block
        block_param = tool.map_tool_result_to_tool_result_block_param(
            tool_result.data,
            tool_use_id,
        )

        yield MessageUpdateLazy(
            message=create_user_message(
                content=[block_param],
                tool_use_result=str(tool_result.data)[:500],
                source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
            ),
            context_modifier=(
                _ContextModifier(
                    tool_use_id=tool_use_id,
                    modify_context=tool_result.context_modifier,
                )
                if tool_result.context_modifier is not None
                else None
            ),
        )

    except Exception as tool_err:
        duration_ms = int((time.time() - start_time) * 1000)
        yield MessageUpdateLazy(
            message=create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"<tool_use_error>Error: {tool_err}</tool_use_error>",
                        "is_error": True,
                    }
                ],
                tool_use_result=f"Error: {tool_err}",
                source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
            )
        )


def _get_abort_signal(tool_use_context: Any) -> Any:
    """Extract abort signal from tool_use_context."""
    if tool_use_context is None:
        return None
    controller = getattr(tool_use_context, "abort_controller", None)
    if controller is None:
        return None
    return getattr(controller, "signal", controller)


async def check_permissions_and_call_tool(
    tool: Any,
    tool_use_id: str,
    raw_input: dict[str, Any],
    tool_use_context: Any,
    can_use_tool: Any = None,
    assistant_message: Any = None,
    message_id: str = "",
    request_id: str | None = None,
    mcp_server_type: McpServerType = None,
    mcp_server_base_url: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Run the full tool execution pipeline:
    1. Pre-tool hooks
    2. Permission check
    3. Tool call
    4. Post-tool hooks

    Yields events as they occur.
    """
    start_time = time.time()
    tool_use = {"name": tool.name, "id": tool_use_id, "input": raw_input}

    async for update in run_tool_use(
        tool_use,
        assistant_message,
        can_use_tool,
        tool_use_context,
    ):
        if update.message is not None:
            msg = update.message
            content = getattr(msg.message, "content", None)
            is_error = any(
                isinstance(b, dict) and b.get("is_error")
                for b in (content if isinstance(content, list) else [])
            )
            yield {
                "type": "tool_error" if is_error else "tool_result",
                "tool_use_id": tool_use_id,
                "result": msg,
                "duration_ms": int((time.time() - start_time) * 1000),
            }
