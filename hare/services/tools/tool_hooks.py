"""
Tool use hooks: pre/post tool execution hooks.

Port of: src/services/tools/toolHooks.ts

The Python port wires into the existing hare.utils.hooks infrastructure:
- AsyncHookRegistry for hook subscription
- _run_single_hook for per-hook execution with multi-type dispatch
- normalize_hook_json_output / resolve_hook_decision for JSON protocol parsing

This module provides the tool-lifecycle orchestration layer that sits between
tool_execution.py and the hook runner internals.
"""

from __future__ import annotations

import time
from typing import Any, AsyncGenerator, Optional

from hare.utils.debug import log_for_debugging
from hare.utils.tool_errors import format_error

# ---------------------------------------------------------------------------
# Helpers: create single-attachment message (mirrors TS createAttachmentMessage)
# ---------------------------------------------------------------------------


def _create_attachment_message(attachment: dict[str, Any]) -> dict[str, Any]:
    """Create a message dict with a single attachment, matching TS createAttachmentMessage.

    Args:
        attachment: A dict with attachment fields (type, hookName, toolUseID, etc.)

    Returns:
        A dict with ``type: "attachment"``, ``attachment``, and a generated uuid.
    """
    import uuid

    return {
        "attachment": attachment,
        "type": "attachment",
        "uuid": str(uuid.uuid4()),
        "timestamp": "",  # callers can override
    }


def _get_pre_tool_hook_blocking_message(
    hook_name: str, blocking_error: Any
) -> str:
    """Format a blocking error from a PreToolUse hook.

    TS: getPreToolHookBlockingMessage — formats: ``{hookName} hook error: {blockingError}``
    """
    err_msg = _extract_blocking_error_text(blocking_error)
    return f"{hook_name} hook error: {err_msg}"


def _get_post_tool_hook_blocking_message(
    hook_name: str, blocking_error: Any
) -> str:
    """Format a blocking error from a PostToolUse hook."""
    err_msg = _extract_blocking_error_text(blocking_error)
    return f"{hook_name} hook error: {err_msg}"


def _get_post_tool_failure_hook_blocking_message(
    hook_name: str, blocking_error: Any
) -> str:
    """Format a blocking error from a PostToolUseFailure hook."""
    err_msg = _extract_blocking_error_text(blocking_error)
    return f"{hook_name} hook error: {err_msg}"


def _extract_blocking_error_text(blocking_error: Any) -> str:
    """Extract a human-readable error string from a blocking error object."""
    if isinstance(blocking_error, str):
        return blocking_error
    if isinstance(blocking_error, dict):
        return blocking_error.get("blockingError", str(blocking_error))
    if hasattr(blocking_error, "blocking_error"):
        return str(blocking_error.blocking_error)
    if hasattr(blocking_error, "blockingError"):
        return str(blocking_error.blockingError)
    return str(blocking_error)


def _is_hook_cancelled_attachment(result: dict[str, Any]) -> bool:
    """Check if a hook result contains a cancelled attachment message.

    TS: result.message?.type === 'attachment' && result.message.attachment.type === 'hook_cancelled'
    """
    msg = result.get("message")
    if not isinstance(msg, dict):
        return False
    if msg.get("type") != "attachment":
        return False
    att = msg.get("attachment")
    if not isinstance(att, dict):
        return False
    return att.get("type") == "hook_cancelled"


def _is_hook_blocking_error_attachment(result: dict[str, Any]) -> bool:
    """Check if result.message is a hook_blocking_error attachment.

    TS: result.message.type === 'attachment' && result.message.attachment.type === 'hook_blocking_error'
    """
    msg = result.get("message")
    if not isinstance(msg, dict):
        return False
    if msg.get("type") != "attachment":
        return False
    att = msg.get("attachment")
    if not isinstance(att, dict):
        return False
    return att.get("type") == "hook_blocking_error"


# ---------------------------------------------------------------------------
# Hook input builders (mirror TS createBaseHookInput + event-specific fields)
# ---------------------------------------------------------------------------


def _build_pre_tool_hook_input(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_use_context: Any,
    permission_mode: str = "",
    mcp_server_type: str | None = None,
    mcp_server_base_url: str | None = None,
) -> dict[str, Any]:
    """Build hook input dict for PreToolUse hooks.

    Mirrors TS PreToolUseHookInput = { ...createBaseHookInput(...),
    hook_event_name: 'PreToolUse', tool_name, tool_input, tool_use_id }.
    """
    base = _create_base_hook_input(tool_use_context, permission_mode)
    base.update(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
        }
    )
    if mcp_server_type:
        base["mcp_server_type"] = mcp_server_type
    if mcp_server_base_url:
        base["mcp_server_base_url"] = mcp_server_base_url
    return base


def _build_post_tool_hook_input(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_response: Any,
    tool_use_context: Any,
    permission_mode: str = "",
    mcp_server_type: str | None = None,
    mcp_server_base_url: str | None = None,
) -> dict[str, Any]:
    """Build hook input dict for PostToolUse hooks.

    Mirrors TS PostToolUseHookInput.
    """
    base = _create_base_hook_input(tool_use_context, permission_mode)
    base.update(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_response": tool_response,
            "tool_use_id": tool_use_id,
        }
    )
    if mcp_server_type:
        base["mcp_server_type"] = mcp_server_type
    if mcp_server_base_url:
        base["mcp_server_base_url"] = mcp_server_base_url
    return base


def _build_post_tool_failure_hook_input(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    error: str,
    is_interrupt: bool | None,
    tool_use_context: Any,
    permission_mode: str = "",
    mcp_server_type: str | None = None,
    mcp_server_base_url: str | None = None,
) -> dict[str, Any]:
    """Build hook input dict for PostToolUseFailure hooks.

    Mirrors TS PostToolUseFailureHookInput.
    """
    base = _create_base_hook_input(tool_use_context, permission_mode)
    base.update(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
            "error": error,
            "is_interrupt": is_interrupt,
        }
    )
    if mcp_server_type:
        base["mcp_server_type"] = mcp_server_type
    if mcp_server_base_url:
        base["mcp_server_base_url"] = mcp_server_base_url
    return base


def _create_base_hook_input(
    tool_use_context: Any,
    permission_mode: str = "",
) -> dict[str, Any]:
    """Create the base hook input dict common to all hook types.

    TS: createBaseHookInput — produces { session_id, transcript_path, cwd, permission_mode, agent_id }.

    Args:
        tool_use_context: ToolUseContext with agentId, options, etc.
        permission_mode: Optional permission mode string.

    Returns:
        Base dict with session/env context fields.
    """
    import os

    session_id = ""
    transcript_path = ""
    cwd = os.getcwd()
    agent_id = None
    agent_type = ""

    if tool_use_context is not None:
        # Extract agent ID (mirrors TS toolUseContext.agentId)
        agent_id = getattr(tool_use_context, "agentId", None)
        if agent_id is None:
            agent_id = getattr(tool_use_context, "agent_id", None)
        # Extract agent type
        agent_type = getattr(tool_use_context, "agentType", "")
        if not agent_type:
            agent_type = getattr(tool_use_context, "agent_type", "")
        # Session ID from context or agent ID fallback
        session_id_attr = getattr(tool_use_context, "sessionId", None)
        if session_id_attr is None:
            session_id_attr = getattr(tool_use_context, "session_id", None)
        session_id = session_id_attr or agent_id or ""
        # CWD from context if available
        ctx_cwd = getattr(tool_use_context, "cwd", None)
        if ctx_cwd:
            cwd = ctx_cwd

    return {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": cwd,
        "permission_mode": permission_mode,
        "agent_id": agent_id,
        "agent_type": agent_type,
    }


def _get_permission_mode(tool_use_context: Any) -> str:
    """Extract permission mode from tool_use_context.

    TS: appState.toolPermissionContext.mode
    """
    if tool_use_context is None:
        return "default"
    try:
        app_state = tool_use_context.getAppState()
        if app_state is not None:
            perm_ctx = getattr(app_state, "toolPermissionContext", None)
            if perm_ctx is not None:
                return getattr(perm_ctx, "mode", "default")
    except Exception:
        pass
    # Fallback: try direct attribute access
    options = getattr(tool_use_context, "options", None)
    if options is not None:
        perm_ctx = getattr(options, "permission_context", None)
        if perm_ctx is not None:
            return getattr(perm_ctx, "mode", "default")
    return "default"


def _get_abort_signal(tool_use_context: Any) -> Any:
    """Extract abort signal from tool_use_context.

    TS: toolUseContext.abortController.signal
    """
    if tool_use_context is None:
        return None
    controller = getattr(tool_use_context, "abortController", None)
    if controller is not None:
        signal = getattr(controller, "signal", None)
        if signal is not None:
            return signal
    controller = getattr(tool_use_context, "abort_controller", None)
    if controller is not None:
        return getattr(controller, "signal", controller)
    return None


def _is_tool_mcp(tool: Any) -> bool:
    """Check if a tool is an MCP tool.

    TS: isMcpTool(tool) — checks tool.isMcp flag.
    Python: checks tool name starts with 'mcp__' or has is_mcp attribute.
    """
    if tool is None:
        return False
    # Check is_mcp / isMcp attribute
    is_mcp = getattr(tool, "is_mcp", None)
    if is_mcp is None:
        is_mcp = getattr(tool, "isMcp", None)
    if is_mcp is True:
        return True
    # Check name prefix
    tool_name = getattr(tool, "name", "") or ""
    return tool_name.startswith("mcp__")


# ---------------------------------------------------------------------------
# Core hook execution pipeline
# ---------------------------------------------------------------------------


async def _execute_tool_hooks(
    hook_event: str,
    tool_name: str,
    tool_use_id: str,
    hook_input: dict[str, Any],
    tool_use_context: Any,
    abort_signal: Any = None,
    timeout_ms: int = 600_000,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute hooks for a tool event.

    This is the shared core that mirrors TS executeHooks() at a reduced scope.
    It uses the Python AsyncHookRegistry to find and run matching hooks.

    Yields dicts with keys: message, blockingError, preventContinuation,
    stopReason, permissionDecision, permissionDecisionReason,
    permissionBehavior, updatedInput, additionalContext, additionalContexts,
    updatedMCPToolOutput, hookSource.
    """
    import asyncio

    from hare.utils.hooks import _run_single_hook
    from hare.utils.hooks.hook_events import HookEvent

    # Map event string to HookEvent literal
    event_map: dict[str, str] = {
        "PreToolUse": "PreToolUse",
        "PostToolUse": "PostToolUse",
        "PostToolUseFailure": "PostToolUseFailure",
    }
    evt: str = event_map.get(hook_event, hook_event)

    # Get matching handlers from registry
    from hare.utils.hooks import _get_matching_hooks

    handlers = _get_matching_hooks(evt)  # type: ignore[arg-type]
    # Settings hooks carry a tool-name matcher (TS: hooks.ts matches the
    # matcher against the tool before running the command). Registered Python
    # callbacks have no matcher and always run.
    from hare.utils.hooks.settings_hooks import matcher_matches_tool

    handlers = [
        handler
        for handler in handlers
        if matcher_matches_tool(getattr(handler, "matcher", None), tool_name)
    ]
    if not handlers:
        return

    # Check abort signal before running
    if abort_signal is not None:
        aborted = getattr(abort_signal, "aborted", False)
        if callable(aborted):
            aborted = aborted()
        if aborted:
            return

    # Build context for hook execution
    hook_name_tag = f"{hook_event}:{tool_name}"

    # Run hooks concurrently (TS: all(hookPromises))
    hook_tasks = []
    for handler in handlers:
        handler_context = dict(hook_input)
        hook_tasks.append(
            _run_single_hook(handler, handler_context, timeout_ms)
        )

    results = await asyncio.gather(*hook_tasks, return_exceptions=True)

    # Process results
    for result in results:
        if isinstance(result, BaseException):
            # Exception from hook execution → non-blocking error
            yield {
                "message": _create_attachment_message({
                    "type": "hook_non_blocking_error",
                    "hookName": hook_name_tag,
                    "toolUseID": tool_use_id,
                    "hookEvent": hook_event,
                    "stderr": f"Failed to run: {str(result)}",
                    "stdout": "",
                    "exitCode": 1,
                }),
            }
            continue

        if not isinstance(result, dict) or not result:
            continue

        # Extract fields from hook result
        message = result.get("message")
        blocking_error = result.get("blockingError")
        prevent_continuation = result.get("preventContinuation")
        stop_reason = result.get("stopReason")
        permission_decision = result.get("permissionDecision")
        permission_decision_reason = result.get("permissionDecisionReason")
        permission_behavior = result.get("permissionBehavior")
        updated_input = result.get("updatedInput")
        additional_context = result.get("additionalContext")
        warning = result.get("warning")
        hook_specific = result.get("hookSpecificOutput")

        # Check for abort cancellation
        if _is_hook_cancelled_attachment(result):
            yield {
                "message": _create_attachment_message({
                    "type": "hook_cancelled",
                    "hookName": hook_name_tag,
                    "toolUseID": tool_use_id,
                    "hookEvent": hook_event,
                }),
            }
            continue

        # Process hook-specific output for updated MCP tool output
        updated_mcp_output = None
        if isinstance(hook_specific, dict):
            if hook_specific.get("hookEventName") == hook_event:
                updated_mcp_output = hook_specific.get("updatedMCPToolOutput")

        # Blocking error → yield both message and blockingError
        if blocking_error is not None:
            yield {
                "blockingError": blocking_error,
                "message": _create_attachment_message({
                    "type": "hook_blocking_error",
                    "hookName": hook_name_tag,
                    "toolUseID": tool_use_id,
                    "hookEvent": hook_event,
                    "blockingError": blocking_error.blocking_error
                    if hasattr(blocking_error, "blocking_error")
                    else str(blocking_error),
                }),
            }
            # For PreToolUse, also create a permission decision
            if hook_event == "PreToolUse":
                denial_msg = _get_pre_tool_hook_blocking_message(
                    hook_name_tag, blocking_error
                )
                yield {
                    "hookPermissionResult": {
                        "behavior": "deny",
                        "message": denial_msg,
                        "decisionReason": {
                            "type": "hook",
                            "hookName": hook_name_tag,
                            "reason": denial_msg,
                        },
                    },
                }
            continue

        # Yield regular message (skip if it's a duplicate hook_blocking_error)
        if message is not None and not _is_hook_blocking_error_attachment(
            result
        ):
            yield {"message": message}

        # Warning
        if warning:
            yield {"warning": warning}

        # Prevent continuation
        if prevent_continuation:
            yield {"preventContinuation": True}
            if stop_reason:
                yield {"stopReason": stop_reason}

        # Permission decision (from hookSpecificOutput.PreToolUse)
        if permission_behavior is not None:
            yield {
                "permissionBehavior": permission_behavior,
                "hookPermissionDecisionReason": permission_decision_reason or "",
                "hookSource": result.get("hookSource", ""),
            }
            # Build permission result
            if permission_behavior == "allow":
                yield {
                    "hookPermissionResult": {
                        "behavior": "allow",
                        "updatedInput": updated_input,
                        "decisionReason": {
                            "type": "hook",
                            "hookName": hook_name_tag,
                            "hookSource": result.get("hookSource", ""),
                            "reason": permission_decision_reason or "",
                        },
                    },
                }
            elif permission_behavior == "deny":
                yield {
                    "hookPermissionResult": {
                        "behavior": "deny",
                        "message": permission_decision_reason
                        or f"Hook {hook_name_tag} denied this tool",
                        "decisionReason": {
                            "type": "hook",
                            "hookName": hook_name_tag,
                            "reason": permission_decision_reason
                            or "Blocked by hook",
                        },
                    },
                }
            elif permission_behavior == "ask":
                yield {
                    "hookPermissionResult": {
                        "behavior": "ask",
                        "updatedInput": updated_input,
                        "message": permission_decision_reason
                        or f"Hook {hook_name_tag} asked for confirmation for this tool",
                        "decisionReason": {
                            "type": "hook",
                            "hookName": hook_name_tag,
                            "hookSource": result.get("hookSource", ""),
                            "reason": permission_decision_reason
                            or "Hook requires confirmation",
                        },
                    },
                }
            else:
                # Treat unknown behaviors as deny
                yield {
                    "hookPermissionResult": {
                        "behavior": permission_behavior,
                        "message": permission_decision_reason
                        or f"Hook {hook_name_tag} {permission_behavior} this tool",
                        "decisionReason": {
                            "type": "hook",
                            "hookName": hook_name_tag,
                            "reason": permission_decision_reason
                            or f"Hook behavior: {permission_behavior}",
                        },
                    },
                }

        # Updated input (passthrough case — no permission decision)
        if (
            updated_input
            and permission_behavior is None
            and isinstance(updated_input, dict)
        ):
            yield {"updatedInput": updated_input}

        # Additional context
        if additional_context and isinstance(additional_context, str):
            yield {
                "additionalContext": additional_context,
            }
            yield {
                "additionalContexts": [additional_context],
            }

        # Updated MCP tool output
        if updated_mcp_output is not None and _is_tool_mcp(
            type("DummyTool", (), {"name": tool_name})
        ):
            yield {"updatedMCPToolOutput": updated_mcp_output}

        # Check for abort after processing this result
        if abort_signal is not None:
            aborted_flag = getattr(abort_signal, "aborted", False)
            if callable(aborted_flag):
                aborted_flag = aborted_flag()
            if aborted_flag:
                yield {
                    "message": _create_attachment_message({
                        "type": "hook_cancelled",
                        "hookName": hook_name_tag,
                        "toolUseID": tool_use_id,
                        "hookEvent": hook_event,
                    }),
                }
                yield {"stop": True}
                return


# ---------------------------------------------------------------------------
# Public API — Pre-tool hooks
# ---------------------------------------------------------------------------


async def run_pre_tool_use_hooks(
    tool_use_context: Any,
    tool: Any,
    processed_input: dict[str, Any],
    tool_use_id: str,
    message_id: str,
    request_id: str | None = None,
    mcp_server_type: str | None = None,
    mcp_server_base_url: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run PreToolUse hooks and yield results.

    TS: runPreToolUseHooks — executes pre-tool hooks and yields structured
    results for permission resolution (allow/deny/ask), updated input,
    additional context, stop/cancel signals.

    The returned async generator yields dicts with keys:
    - ``type``: the result kind (one of 'message', 'hookPermissionResult',
      'hookUpdatedInput', 'preventContinuation', 'stopReason',
      'additionalContext', 'stop')
    - Corresponding payload for that type.

    Args:
        tool_use_context: ToolUseContext with app state, abort controller, etc.
        tool: The Tool being invoked (has name, queryTracking, etc.)
        processed_input: The validated/normalized tool input dict.
        tool_use_id: Unique ID for this tool_use block.
        message_id: The message UUID containing this tool_use.
        request_id: Optional API request ID for telemetry.
        mcp_server_type: Optional MCP server type for MCP tools.
        mcp_server_base_url: Optional MCP server base URL for MCP tools.
    """
    if tool is None:
        return

    tool_name = getattr(tool, "name", "") if hasattr(tool, "name") else str(tool)

    # Fast-path: skip if no hook handlers registered for PreToolUse
    from hare.utils.hooks import _get_matching_hooks

    handlers = _get_matching_hooks("PreToolUse")
    if not handlers:
        return

    hook_start_time = time.time()
    abort_signal = _get_abort_signal(tool_use_context)

    # Check abort before starting
    if abort_signal is not None:
        aborted = getattr(abort_signal, "aborted", False)
        if callable(aborted):
            aborted = aborted()
        if aborted:
            yield {
                "type": "message",
                "message": _create_attachment_message({
                    "type": "hook_cancelled",
                    "hookName": f"PreToolUse:{tool_name}",
                    "toolUseID": tool_use_id,
                    "hookEvent": "PreToolUse",
                }),
            }
            yield {"type": "stop"}
            return

    permission_mode = _get_permission_mode(tool_use_context)
    hook_input = _build_pre_tool_hook_input(
        tool_name,
        tool_use_id,
        processed_input,
        tool_use_context,
        permission_mode,
        mcp_server_type,
        mcp_server_base_url,
    )

    # Build hook name tag for error messages
    hook_name_tag = f"PreToolUse:{tool_name}"

    try:
        async for result in _execute_tool_hooks(
            "PreToolUse",
            tool_name,
            tool_use_id,
            hook_input,
            tool_use_context,
            abort_signal,
        ):
            # Process each result and yield typed dicts

            # Handle stop signal first
            if result.get("stop"):
                yield {"type": "stop"}
                return

            # Abort cancellation message
            msg = result.get("message")
            if msg is not None and isinstance(msg, dict):
                if msg.get("type") == "attachment":
                    att = msg.get("attachment", {})
                    if att.get("type") == "hook_cancelled":
                        yield {"type": "message", "message": msg}
                        yield {"type": "stop"}
                        return

            # Blocking error → deny permission
            blocking_error = result.get("blockingError")
            if blocking_error is not None:
                denial_msg = _get_pre_tool_hook_blocking_message(
                    hook_name_tag, blocking_error
                )
                yield {
                    "type": "hookPermissionResult",
                    "hookPermissionResult": {
                        "behavior": "deny",
                        "message": denial_msg,
                        "decisionReason": {
                            "type": "hook",
                            "hookName": hook_name_tag,
                            "reason": denial_msg,
                        },
                    },
                }
                # Also yield a blocking error attachment
                if msg is not None:
                    yield {"type": "message", "message": msg}
                continue

            # Prevent continuation
            if result.get("preventContinuation"):
                yield {
                    "type": "preventContinuation",
                    "shouldPreventContinuation": True,
                }
                stop_reason = result.get("stopReason")
                if stop_reason:
                    yield {"type": "stopReason", "stopReason": stop_reason}
                continue

            # Permission behavior (allow/deny/ask)
            permission_behavior = result.get("permissionBehavior")
            if permission_behavior is not None:
                permission_result = result.get("hookPermissionResult")
                if permission_result:
                    yield {
                        "type": "hookPermissionResult",
                        "hookPermissionResult": permission_result,
                    }
                else:
                    # Build synthetic permission result
                    hook_source = result.get("hookSource", "")
                    decision_reason = result.get(
                        "hookPermissionDecisionReason", ""
                    )
                    if permission_behavior == "allow":
                        yield {
                            "type": "hookPermissionResult",
                            "hookPermissionResult": {
                                "behavior": "allow",
                                "updatedInput": result.get("updatedInput"),
                                "decisionReason": {
                                    "type": "hook",
                                    "hookName": hook_name_tag,
                                    "hookSource": hook_source,
                                    "reason": decision_reason,
                                },
                            },
                        }
                    elif permission_behavior == "ask":
                        yield {
                            "type": "hookPermissionResult",
                            "hookPermissionResult": {
                                "behavior": "ask",
                                "updatedInput": result.get("updatedInput"),
                                "message": decision_reason
                                or f"Hook {hook_name_tag} asked for confirmation for this tool",
                                "decisionReason": {
                                    "type": "hook",
                                    "hookName": hook_name_tag,
                                    "hookSource": hook_source,
                                    "reason": decision_reason
                                    or "Hook requires confirmation",
                                },
                            },
                        }
                    else:
                        yield {
                            "type": "hookPermissionResult",
                            "hookPermissionResult": {
                                "behavior": permission_behavior,
                                "message": decision_reason
                                or f"Hook {hook_name_tag} {permission_behavior} this tool",
                                "decisionReason": {
                                    "type": "hook",
                                    "hookName": hook_name_tag,
                                    "reason": decision_reason
                                    or f"Hook behavior: {permission_behavior}",
                                },
                            },
                        }
                continue

            # Updated input (passthrough — no permission decision)
            updated_input = result.get("updatedInput")
            if updated_input is not None and isinstance(updated_input, dict):
                yield {
                    "type": "hookUpdatedInput",
                    "updatedInput": updated_input,
                }

            # Additional context
            additional_contexts = result.get("additionalContexts")
            if additional_contexts and isinstance(additional_contexts, list):
                for ctx in additional_contexts:
                    if isinstance(ctx, str):
                        yield {
                            "type": "additionalContext",
                            "message": {
                                "message": _create_attachment_message({
                                    "type": "hook_additional_context",
                                    "content": ctx,
                                    "hookName": hook_name_tag,
                                    "toolUseID": tool_use_id,
                                    "hookEvent": "PreToolUse",
                                }),
                            },
                        }

            # General message
            if msg is not None and not _is_hook_blocking_error_attachment(
                result
            ):
                yield {"type": "message", "message": msg}

            # Check abort after processing
            if abort_signal is not None:
                aborted = getattr(abort_signal, "aborted", False)
                if callable(aborted):
                    aborted = aborted()
                if aborted:
                    yield {
                        "type": "message",
                        "message": _create_attachment_message({
                            "type": "hook_cancelled",
                            "hookName": hook_name_tag,
                            "toolUseID": tool_use_id,
                            "hookEvent": "PreToolUse",
                        }),
                    }
                    yield {"type": "stop"}
                    return

    except Exception as error:
        # Log and yield error, then stop
        try:
            from hare.utils.log import log_error as log_err
        except ImportError:
            log_err = None

        if log_err is not None:
            log_err(error)

        yield {
            "type": "message",
            "message": _create_attachment_message({
                "type": "hook_error_during_execution",
                "content": format_error(error),
                "hookName": hook_name_tag,
                "toolUseID": tool_use_id,
                "hookEvent": "PreToolUse",
            }),
        }
        yield {"type": "stop"}

    finally:
        duration_ms = int((time.time() - hook_start_time) * 1000)
        log_for_debugging(
            f"PreToolUse hooks for {tool_name} completed in {duration_ms}ms"
        )


# ---------------------------------------------------------------------------
# Public API — Post-tool hooks
# ---------------------------------------------------------------------------


async def run_post_tool_use_hooks(
    tool_use_context: Any,
    tool: Any,
    tool_use_id: str,
    message_id: str,
    tool_input: dict[str, Any],
    tool_response: Any,
    request_id: str | None = None,
    mcp_server_type: str | None = None,
    mcp_server_base_url: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run PostToolUse hooks and yield results.

    TS: runPostToolUseHooks — executes post-tool hooks and yields messages,
    blocking errors, additional context, and updated MCP tool output.

    Yields dicts with keys matching TS PostToolUseHooksResult:
    - ``message``: an attachment or progress message dict
    - ``updatedMCPToolOutput``: updated output for MCP tools

    Args:
        tool_use_context: ToolUseContext after tool execution.
        tool: The Tool that was executed.
        tool_use_id: Unique ID for this tool_use block.
        message_id: The message UUID containing this tool_use.
        tool_input: The input that was passed to the tool.
        tool_response: The response from the tool execution.
        request_id: Optional API request ID for telemetry.
        mcp_server_type: Optional MCP server type for MCP tools.
        mcp_server_base_url: Optional MCP server base URL for MCP tools.
    """
    if tool is None:
        return

    tool_name = getattr(tool, "name", "") if hasattr(tool, "name") else str(tool)

    # Fast-path: skip if no hook handlers
    from hare.utils.hooks import _get_matching_hooks

    handlers = _get_matching_hooks("PostToolUse")
    if not handlers:
        return

    post_tool_start_time = time.time()
    abort_signal = _get_abort_signal(tool_use_context)
    hook_name_tag = f"PostToolUse:{tool_name}"

    try:
        permission_mode = _get_permission_mode(tool_use_context)
        hook_input = _build_post_tool_hook_input(
            tool_name,
            tool_use_id,
            tool_input,
            tool_response,
            tool_use_context,
            permission_mode,
            mcp_server_type,
            mcp_server_base_url,
        )

        # Track current tool output (may be updated by hooks)
        tool_output = tool_response

        async for result in _execute_tool_hooks(
            "PostToolUse",
            tool_name,
            tool_use_id,
            hook_input,
            tool_use_context,
            abort_signal,
        ):
            # Abort cancellation
            if result.get("stop"):
                return

            msg = result.get("message")

            # Hook cancelled → yield cancelled message and continue
            if _is_hook_cancelled_attachment(result):
                if msg is not None:
                    yield {"message": msg}
                continue

            # Yield regular message (skip hook_blocking_error duplicates — #31301)
            if msg is not None and not _is_hook_blocking_error_attachment(
                result
            ):
                yield {"message": msg}

            # Blocking error → create attachment message (#31301: skip
            # duplicate hook_blocking_error in result.message)
            blocking_error = result.get("blockingError")
            if blocking_error is not None:
                yield {
                    "message": _create_attachment_message({
                        "type": "hook_blocking_error",
                        "hookName": hook_name_tag,
                        "toolUseID": tool_use_id,
                        "hookEvent": "PostToolUse",
                        "blockingError": _extract_blocking_error_text(
                            blocking_error
                        ),
                    }),
                }

            # Prevent continuation → stop reason message, then stop
            if result.get("preventContinuation"):
                stop_reason = result.get("stopReason") or (
                    "Execution stopped by PostToolUse hook"
                )
                yield {
                    "message": _create_attachment_message({
                        "type": "hook_stopped_continuation",
                        "message": stop_reason,
                        "hookName": hook_name_tag,
                        "toolUseID": tool_use_id,
                        "hookEvent": "PostToolUse",
                    }),
                }
                return

            # Additional context
            additional_contexts = result.get("additionalContexts")
            if additional_contexts and isinstance(additional_contexts, list):
                for ctx in additional_contexts:
                    if isinstance(ctx, str):
                        yield {
                            "message": _create_attachment_message({
                                "type": "hook_additional_context",
                                "content": ctx,
                                "hookName": hook_name_tag,
                                "toolUseID": tool_use_id,
                                "hookEvent": "PostToolUse",
                            }),
                        }

            # Updated MCP tool output
            updated_mcp = result.get("updatedMCPToolOutput")
            if updated_mcp is not None and _is_tool_mcp(tool):
                tool_output = updated_mcp
                yield {"updatedMCPToolOutput": tool_output}

            # Abort after processing
            if abort_signal is not None:
                aborted = getattr(abort_signal, "aborted", False)
                if callable(aborted):
                    aborted = aborted()
                if aborted:
                    yield {
                        "message": _create_attachment_message({
                            "type": "hook_cancelled",
                            "hookName": hook_name_tag,
                            "toolUseID": tool_use_id,
                            "hookEvent": "PostToolUse",
                        }),
                    }
                    return

    except Exception as error:
        post_tool_duration_ms = int(
            (time.time() - post_tool_start_time) * 1000
        )
        try:
            from hare.utils.log import log_error as log_err
        except ImportError:
            log_err = None

        if log_err is not None:
            log_err(error)

        yield {
            "message": _create_attachment_message({
                "type": "hook_error_during_execution",
                "content": format_error(error),
                "hookName": hook_name_tag,
                "toolUseID": tool_use_id,
                "hookEvent": "PostToolUse",
            }),
        }


# ---------------------------------------------------------------------------
# Public API — Post-tool failure hooks
# ---------------------------------------------------------------------------


async def run_post_tool_use_failure_hooks(
    tool_use_context: Any,
    tool: Any,
    tool_use_id: str,
    message_id: str,
    processed_input: dict[str, Any],
    error: str,
    is_interrupt: bool | None = None,
    request_id: str | None = None,
    mcp_server_type: str | None = None,
    mcp_server_base_url: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run PostToolUseFailure hooks and yield results.

    TS: runPostToolUseFailureHooks — invoked when a tool call fails.
    Hooks can provide additional context, blocking feedback, or cancellation.

    Yields dicts with keys:
    - ``message``: an attachment message dict (cancelled, blocking_error,
      additional_context, or hook_error_during_execution).

    Args:
        tool_use_context: ToolUseContext after tool failure.
        tool: The Tool that failed.
        tool_use_id: Unique ID for this tool_use block.
        message_id: The message UUID containing this tool_use.
        processed_input: The validated tool input that was attempted.
        error: Error message from the failed tool call.
        is_interrupt: Whether the tool was interrupted by the user.
        request_id: Optional API request ID for telemetry.
        mcp_server_type: Optional MCP server type for MCP tools.
        mcp_server_base_url: Optional MCP server base URL for MCP tools.
    """
    if tool is None:
        return

    tool_name = getattr(tool, "name", "") if hasattr(tool, "name") else str(tool)

    # Fast-path: skip if no hook handlers
    from hare.utils.hooks import _get_matching_hooks

    handlers = _get_matching_hooks("PostToolUseFailure")
    if not handlers:
        return

    post_tool_start_time = time.time()
    abort_signal = _get_abort_signal(tool_use_context)
    hook_name_tag = f"PostToolUseFailure:{tool_name}"

    try:
        permission_mode = _get_permission_mode(tool_use_context)
        hook_input = _build_post_tool_failure_hook_input(
            tool_name,
            tool_use_id,
            processed_input,
            error,
            is_interrupt,
            tool_use_context,
            permission_mode,
            mcp_server_type,
            mcp_server_base_url,
        )

        async for result in _execute_tool_hooks(
            "PostToolUseFailure",
            tool_name,
            tool_use_id,
            hook_input,
            tool_use_context,
            abort_signal,
        ):
            # Abort check: hook was cancelled during execution
            if _is_hook_cancelled_attachment(result):
                msg = result.get("message")
                if msg is not None:
                    yield {"message": msg}
                continue

            # Yield regular message (skip duplicate hook_blocking_error)
            msg = result.get("message")
            if msg is not None and not _is_hook_blocking_error_attachment(
                result
            ):
                yield {"message": msg}

            # Blocking error → create attachment
            blocking_error = result.get("blockingError")
            if blocking_error is not None:
                yield {
                    "message": _create_attachment_message({
                        "type": "hook_blocking_error",
                        "hookName": hook_name_tag,
                        "toolUseID": tool_use_id,
                        "hookEvent": "PostToolUseFailure",
                        "blockingError": _extract_blocking_error_text(
                            blocking_error
                        ),
                    }),
                }

            # Additional context
            additional_contexts = result.get("additionalContexts")
            if additional_contexts and isinstance(additional_contexts, list):
                for ctx in additional_contexts:
                    if isinstance(ctx, str):
                        yield {
                            "message": _create_attachment_message({
                                "type": "hook_additional_context",
                                "content": ctx,
                                "hookName": hook_name_tag,
                                "toolUseID": tool_use_id,
                                "hookEvent": "PostToolUseFailure",
                            }),
                        }

            # Stop signal from abort
            if result.get("stop"):
                return

    except Exception as outer_error:
        try:
            from hare.utils.log import log_error as log_err
        except ImportError:
            log_err = None

        if log_err is not None:
            log_err(outer_error)


# ---------------------------------------------------------------------------
# Public API — Permission decision resolution
# ---------------------------------------------------------------------------


async def resolve_hook_permission_decision(
    hook_permission_result: dict[str, Any] | None,
    tool: Any,
    input_args: dict[str, Any],
    tool_use_context: Any,
    can_use_tool: Any,
    assistant_message: Any,
    tool_use_id: str,
) -> dict[str, Any]:
    """Resolve a PreToolUse hook's permission result into a final PermissionDecision.

    TS: resolveHookPermissionDecision — the full permission resolution pipeline:
    - Hook 'allow' does NOT bypass settings.json deny/ask rules
    - Interactive tools must still go through canUseTool
    - Hook deny is final (skip further checks)
    - Hook 'ask' passes through as forceDecision

    Shared by toolExecution.ts (main query loop) and REPLTool/toolWrappers.ts
    (REPL inner calls) to keep permission semantics consistent.

    Args:
        hook_permission_result: The HookPermissionResult from run_pre_tool_use_hooks.
            None means no hook made a decision.
        tool: The Tool being invoked.
        input_args: The tool input arguments.
        tool_use_context: The ToolUseContext.
        can_use_tool: Callable for interactive permission checks.
        assistant_message: The assistant message containing this tool_use.
        tool_use_id: Unique ID for this tool_use block.

    Returns:
        A dict with ``decision`` (PermissionDecision) and ``input`` (tool input).
    """
    if hook_permission_result is None:
        # No hook decision → pass through
        return {"decision": {"behavior": "passthrough"}, "input": input_args}

    behavior = hook_permission_result.get("behavior")

    # Resolve requiresInteraction (TS: tool.requiresUserInteraction?.())
    requires_interaction = False
    if hasattr(tool, "requires_user_interaction"):
        req_interact = tool.requires_user_interaction
        requires_interaction = (
            req_interact() if callable(req_interact) else bool(req_interact)
        )
    elif hasattr(tool, "requiresUserInteraction"):
        req_interact = tool.requiresUserInteraction
        requires_interaction = (
            req_interact() if callable(req_interact) else bool(req_interact)
        )

    # Resolve requireCanUseTool (TS: toolUseContext.requireCanUseTool)
    require_can_use_tool = getattr(
        tool_use_context, "requireCanUseTool", False
    )
    if not require_can_use_tool:
        require_can_use_tool = getattr(
            tool_use_context, "require_can_use_tool", False
        )

    # ---- HOOK ALLOW ----
    if behavior == "allow":
        hook_input = (
            hook_permission_result.get("updatedInput")
            or hook_permission_result.get("updated_input")
            or input_args
        )

        # Check if hook satisfied user interaction
        interaction_satisfied = (
            requires_interaction
            and hook_permission_result.get("updatedInput") is not None
        )

        # If tool requires interaction and hook didn't satisfy it,
        # or requireCanUseTool is set → go through canUseTool
        if (
            requires_interaction and not interaction_satisfied
        ) or require_can_use_tool:
            log_for_debugging(
                f"Hook approved tool use for {_tool_display_name(tool)}, "
                f"but canUseTool is required"
            )
            decision = (
                await can_use_tool(
                    tool,
                    hook_input,
                    tool_use_context,
                    assistant_message,
                    tool_use_id,
                )
                if can_use_tool
                else {"behavior": "passthrough"}
            )
            return {"decision": decision, "input": hook_input}

        # Hook allow skips interactive prompt, but deny/ask rules still apply.
        try:
            rule_check = await _check_rule_based_permissions(
                tool, hook_input, tool_use_context
            )
        except Exception:
            rule_check = None

        if rule_check is None:
            if interaction_satisfied:
                log_for_debugging(
                    f"Hook satisfied user interaction for {_tool_display_name(tool)}"
                    f" via updatedInput"
                )
            else:
                log_for_debugging(
                    f"Hook approved tool use for {_tool_display_name(tool)},"
                    f" bypassing permission prompt"
                )
            return {"decision": hook_permission_result, "input": hook_input}

        if rule_check.get("behavior") == "deny":
            log_for_debugging(
                f"Hook approved tool use for {_tool_display_name(tool)}, "
                f"but deny rule overrides: {rule_check.get('message', '')}"
            )
            return {"decision": rule_check, "input": hook_input}

        # ask rule → dialog required despite hook approval
        if rule_check.get("behavior") == "ask":
            log_for_debugging(
                f"Hook approved tool use for {_tool_display_name(tool)}, "
                f"but ask rule requires prompt"
            )
            decision = (
                await can_use_tool(
                    tool,
                    hook_input,
                    tool_use_context,
                    assistant_message,
                    tool_use_id,
                )
                if can_use_tool
                else rule_check
            )
            return {"decision": decision, "input": hook_input}

        # No rule match → hook decision stands
        return {"decision": hook_permission_result, "input": hook_input}

    # ---- HOOK DENY ----
    if behavior == "deny":
        log_for_debugging(
            f"Hook denied tool use for {_tool_display_name(tool)}"
        )
        return {"decision": hook_permission_result, "input": input_args}

    # ---- HOOK ASK (or unknown) ----
    # No hook decision or 'ask' → normal permission flow, possibly with
    # forceDecision so the dialog shows the hook's ask message.
    force_decision = (
        hook_permission_result if behavior == "ask" else None
    )
    ask_input = (
        hook_permission_result.get("updatedInput")
        or hook_permission_result.get("updated_input")
        or input_args
    )

    if can_use_tool is not None:
        decision = await can_use_tool(
            tool,
            ask_input,
            tool_use_context,
            assistant_message,
            tool_use_id,
            force_decision,
        )
    else:
        decision = {"behavior": "passthrough"}

    return {"decision": decision, "input": ask_input}


async def _check_rule_based_permissions(
    tool: Any,
    input: dict[str, Any],
    tool_use_context: Any,
) -> dict[str, Any] | None:
    """Check rule-based permissions (deny/ask/allow rules from settings).

    TS: checkRuleBasedPermissions — returns PermissionResult or null.

    Returns None when no rule matches (passthrough), or a dict with
    behavior ("allow", "deny", "ask") and optional message.
    """
    # Content-level rules (e.g. Bash(touch *)) are matched by the tool's own
    # check_permissions, exactly as in the main pipeline; the rule-based helper
    # below only sees tool-level rules and would answer 'ask' for a content
    # deny, sending the call back through can_use_tool and recording a denial
    # the reference does not report.
    try:
        tool_result = await tool.check_permissions(input, tool_use_context)
        tool_behavior = getattr(tool_result, "behavior", None)
        if tool_behavior in ("deny", "ask"):
            return {
                "behavior": tool_behavior,
                "message": getattr(tool_result, "message", ""),
            }
    except Exception:
        pass

    try:
        from hare.utils.permissions.permissions import check_rule_based_permissions as _check

        result = _check(tool, input, _extract_permission_context(tool_use_context))
        behavior = getattr(result, "behavior", None)
        if behavior == "passthrough":
            return None
        message = getattr(result, "message", "")
        return {"behavior": behavior, "message": message}
    except Exception:
        return None


def _extract_permission_context(tool_use_context: Any) -> Any:
    """Extract ToolPermissionContext from tool_use_context.

    TS: toolUseContext.getAppState().toolPermissionContext
    Falls back to a default context with mode='default'.
    """
    from hare.app_types.permissions import ToolPermissionContext

    if tool_use_context is None:
        return ToolPermissionContext(mode="default")

    try:
        app_state = tool_use_context.getAppState()
        if app_state is not None:
            perm_ctx = getattr(app_state, "toolPermissionContext", None)
            if perm_ctx is not None:
                return perm_ctx
    except Exception:
        pass

    # Fallback: check options
    options = getattr(tool_use_context, "options", None)
    if options is not None:
        perm_ctx = getattr(options, "permission_context", None)
        if perm_ctx is not None:
            return perm_ctx
        commands = getattr(options, "commands", []) or []
        if commands:
            perm_ctx = getattr(commands[0], "permission_context", None)
            if perm_ctx is not None:
                return perm_ctx

    return ToolPermissionContext(mode="default")


def _tool_display_name(tool: Any) -> str:
    """Get the display name for a tool."""
    if hasattr(tool, "name"):
        return str(tool.name)
    return str(tool)
