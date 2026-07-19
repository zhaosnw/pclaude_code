"""
Tool execution orchestration.

Port of: src/services/tools/toolExecution.ts (1745 lines TS — partial port).

Implements the core tool execution loop: lookup → validate → permission check
→ tool.call() → result mapping → MessageUpdateLazy yield.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Optional, Union

from hare.utils.messages import create_user_message
from hare.tool import find_tool_by_name

McpServerType = Optional[str]

logger = logging.getLogger(__name__)


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

    # PreToolUse hooks run before the permission check and can decide it
    # outright (toolExecution.ts:800 → hookPermissionResult wins over the
    # rule-based flow). Without this the whole hook pipeline was dead code:
    # nothing invoked run_pre_tool_use_hooks.
    hook_permission: Any = None
    try:
        from hare.services.tools.tool_hooks import run_pre_tool_use_hooks

        async for hook_result in run_pre_tool_use_hooks(
            tool_use_context,
            tool,
            tool_input,
            tool_use_id,
            getattr(assistant_message, "uuid", "") or "",
        ):
            kind = hook_result.get("type")
            if kind == "hookPermissionResult":
                hook_permission = hook_result.get("hookPermissionResult")
            elif kind == "hookUpdatedInput":
                updated = hook_result.get("updatedInput")
                if isinstance(updated, dict):
                    tool_input = updated
    except ImportError:
        # Distinct from the runtime-failure catch below: an ImportError here
        # means our own wiring is broken (a rename/refactor of tool_hooks),
        # not that some external hook misbehaved. Still must not kill the
        # turn, but it must not look identical to an ordinary hook failure
        # either — log it so a broken import doesn't stay invisible.
        logger.error("PreToolUse hook import failed; hooks disabled for this call", exc_info=True)
        hook_permission = None
    except Exception:  # noqa: BLE001 - a broken hook must not kill the turn
        hook_permission = None

    # A hook decision does not simply win: hook 'allow' still loses to a
    # settings deny/ask rule (resolveHookPermissionDecision). This resolver
    # already encoded that and had no caller.
    #
    # resolve_hook_permission_decision() calls can_use_tool() (and, through
    # it, tool.check_permissions()) in several branches with no guard of its
    # own, so a buggy/misconfigured tool can raise here. Mirroring the
    # run_pre_tool_use_hooks guard above: a broken hook-permission resolution
    # must not kill the turn either. On failure we treat it exactly like
    # hook_permission was None to begin with — falling through to the normal
    # rule-based permission flow below — rather than failing closed, for
    # consistency with the sibling hook-execution guard.
    if hook_permission is not None:
        try:
            from hare.services.tools.tool_hooks import resolve_hook_permission_decision

            resolved = await resolve_hook_permission_decision(
                hook_permission,
                tool,
                tool_input,
                tool_use_context,
                can_use_tool,
                assistant_message,
                tool_use_id,
            )
            decision = resolved.get("decision") or {}
            resolved_input = resolved.get("input", tool_input)
            # Every result on this path is a plain dict; getattr() would silently
            # read the "allow" default and let a denied tool run.
            behavior = (
                decision.get("behavior", "allow")
                if isinstance(decision, dict)
                else getattr(decision, "behavior", "allow")
            )
        except ImportError:
            logger.error(
                "resolve_hook_permission_decision import failed; hook permission disabled for this call",
                exc_info=True,
            )
            hook_permission = None
        except Exception:  # noqa: BLE001 - a broken hook-permission resolution must not kill the turn
            hook_permission = None
        else:
            tool_input = resolved_input
            if behavior in ("deny", "ask"):
                reason = (
                    decision.get("message", "Blocked by hook")
                    if isinstance(decision, dict)
                    else getattr(decision, "message", "Blocked by hook")
                )
                # The released CLI reports a hook-blocked tool in permission_denials
                # (2.1.87 did not). Denials are recorded by the engine's canUseTool
                # wrapper, and this path skips it, so replay the decision through
                # can_use_tool as a forced one purely to register it. The tool_result
                # still carries the hook's own message.
                if can_use_tool is not None:
                    try:
                        await can_use_tool(
                            tool,
                            tool_input,
                            tool_use_context,
                            assistant_message,
                            tool_use_id,
                            behavior,
                        )
                    except Exception:  # noqa: BLE001 - reporting must not block
                        pass
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
                        source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
                    )
                )
                return
            # 'passthrough' means the hook made no decision — fall through to the
            # normal permission check below.
            if behavior == "allow":
                hook_permission = decision
            else:
                hook_permission = None

    # Permission check
    try:
        if can_use_tool is not None and hook_permission is None:
            permission = await can_use_tool(
                tool,
                tool_input,
                tool_use_context,
                assistant_message,
                tool_use_id,
                None,
            )
            # toolExecution.ts:995 blocks on any non-allow decision, so an
            # 'ask' that reaches here (no interactive prompt in headless) must
            # stop the tool, not run it. 'passthrough' is hare's "no rule
            # matched" sentinel — the reference resolves that case through the
            # auto-mode classifier and runs the tool, so it stays allowed here.
            if getattr(permission, "behavior", "allow") in ("deny", "ask"):
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

        # PostToolUse hooks run after a successful call (toolExecution.ts:1483),
        # PostToolUseFailure after a failed one (:1700). The reference's tools
        # throw on failure, so the split falls out of try/except; hare's tools
        # return a result carrying is_error instead, so key off that — otherwise
        # a failed tool wrongly fired PostToolUse and never PostToolUseFailure.
        failed = isinstance(block_param, dict) and bool(block_param.get("is_error"))
        try:
            from hare.services.tools.tool_hooks import (
                run_post_tool_use_failure_hooks,
                run_post_tool_use_hooks,
            )

            if failed:
                hook_stream = run_post_tool_use_failure_hooks(
                    tool_use_context,
                    tool,
                    tool_use_id,
                    getattr(assistant_message, "uuid", "") or "",
                    tool_input,
                    str(block_param.get("content", "")),
                )
            else:
                hook_stream = run_post_tool_use_hooks(
                    tool_use_context,
                    tool,
                    tool_use_id,
                    getattr(assistant_message, "uuid", "") or "",
                    tool_input,
                    tool_result.data,
                )
            async for hook_result in hook_stream:
                hook_message = hook_result.get("message")
                if hook_message is not None:
                    yield MessageUpdateLazy(message=hook_message)
        except ImportError:
            logger.error(
                "PostToolUse/PostToolUseFailure hook import failed; hooks disabled for this call",
                exc_info=True,
            )
        except Exception:  # noqa: BLE001 - a broken hook must not fail the tool
            pass

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

        # PostToolUseFailure, like the other tool hooks, had no caller at all.
        try:
            from hare.services.tools.tool_hooks import (
                run_post_tool_use_failure_hooks,
            )

            async for hook_result in run_post_tool_use_failure_hooks(
                tool_use_context,
                tool,
                tool_use_id,
                getattr(assistant_message, "uuid", "") or "",
                tool_input,
                str(tool_err),
            ):
                hook_message = hook_result.get("message")
                if hook_message is not None:
                    yield MessageUpdateLazy(message=hook_message)
        except ImportError:
            logger.error(
                "PostToolUseFailure hook import failed; hooks disabled for this call",
                exc_info=True,
            )
        except Exception:  # noqa: BLE001 - a broken hook must not mask the error
            pass


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
