"""
/compact command - compact conversation history.

Port of: src/commands/compact/compact.ts + index.ts

Pipeline:
  1. Filter messages to post-compact-boundary
  2. Try session-memory compaction (if no custom instructions)
  3. If reactive-only mode, route through reactive compact
  4. Otherwise run microcompact then traditional compactConversation
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "compact"
DESCRIPTION = "Compact conversation history to free up context"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Execute the /compact command.

    Returns a dict with type='compact', compactionResult, and displayText.
    """
    abort_controller = context.get("abortController")
    messages = context.get("messages", [])
    agent_id = context.get("agentId", "")

    # Filter messages to post-compact-boundary
    get_messages_after_compact_boundary = context.get(
        "get_messages_after_compact_boundary"
    )
    if get_messages_after_compact_boundary:
        messages = get_messages_after_compact_boundary(messages)

    if not messages:
        return {"type": "text", "value": "No messages to compact"}

    custom_instructions = args.strip() if args else ""

    # Services from context
    try_session_memory_compaction = context.get("try_session_memory_compaction")
    compact_conversation_fn = context.get("compact_conversation")
    microcompact_messages_fn = context.get("microcompact_messages")
    run_post_compact_cleanup = context.get("run_post_compact_cleanup")
    suppress_compact_warning = context.get("suppress_compact_warning")
    set_last_summarized_message_id = context.get("set_last_summarized_message_id")
    mark_post_compaction = context.get("mark_post_compaction")
    execute_pre_compact_hooks = context.get("execute_pre_compact_hooks")
    get_upgrade_message = context.get("get_upgrade_message")
    reactive_compact = context.get("reactive_compact")

    try:
        # 1. Try session memory compaction first if no custom instructions
        if not custom_instructions and try_session_memory_compaction:
            session_memory_result = await try_session_memory_compaction(
                messages, agent_id
            )
            if session_memory_result:
                if run_post_compact_cleanup:
                    run_post_compact_cleanup()
                if mark_post_compaction:
                    mark_post_compaction()
                if suppress_compact_warning:
                    suppress_compact_warning()

                display_text = build_display_text(context)
                return {
                    "type": "compact",
                    "compactionResult": session_memory_result,
                    "displayText": display_text,
                }

        # 2. Reactive-only mode
        if (
            reactive_compact
            and reactive_compact.get("isReactiveOnlyMode", lambda: False)()
        ):
            return await _compact_via_reactive(
                messages, context, custom_instructions, reactive_compact
            )

        # 3. Traditional compaction: microcompact first, then compactConversation
        if microcompact_messages_fn:
            microcompact_result = await microcompact_messages_fn(messages, context)
            messages_for_compact = microcompact_result.get("messages", messages)
        else:
            messages_for_compact = messages

        if not compact_conversation_fn:
            # Fallback: return basic compact signal
            if suppress_compact_warning:
                suppress_compact_warning()
            return {
                "type": "compact",
                "compactionResult": {"messages": messages_for_compact},
                "displayText": build_display_text(context),
            }

        cache_sharing_params = await _get_cache_sharing_params(
            context, messages_for_compact
        )

        result = await compact_conversation_fn(
            messages_for_compact,
            context,
            cache_sharing_params,
            False,
            custom_instructions,
            False,
        )

        if set_last_summarized_message_id:
            set_last_summarized_message_id(None)

        if suppress_compact_warning:
            suppress_compact_warning()

        if run_post_compact_cleanup:
            run_post_compact_cleanup()

        return {
            "type": "compact",
            "compactionResult": result,
            "displayText": build_display_text(
                context, result.get("userDisplayMessage")
            ),
        }

    except Exception as e:
        error_msg = str(e)
        if "cancel" in error_msg.lower():
            return {"type": "text", "value": "Compaction canceled."}
        if "not enough messages" in error_msg.lower():
            return {"type": "text", "value": error_msg}
        if "incomplete" in error_msg.lower():
            return {"type": "text", "value": error_msg}
        return {"type": "text", "value": f"Error during compaction: {error_msg}"}


async def _compact_via_reactive(
    messages: list[dict[str, Any]],
    context: dict[str, Any],
    custom_instructions: str,
    reactive_compact: dict[str, Any],
) -> dict[str, Any]:
    """Route compaction through the reactive compact path."""
    execute_pre_compact_hooks = context.get("execute_pre_compact_hooks")
    abort_controller = context.get("abortController")

    hook_result = {"userDisplayMessage": None, "newCustomInstructions": None}
    if execute_pre_compact_hooks:
        signal = abort_controller.signal if abort_controller else None
        hook_result = await execute_pre_compact_hooks(
            {"trigger": "manual", "customInstructions": custom_instructions or None},
            signal,
        )

    merged_instructions = merge_hook_instructions(
        custom_instructions, hook_result.get("newCustomInstructions")
    )

    cache_safe_params = await _get_cache_sharing_params(context, messages)

    outcome = await reactive_compact["reactiveCompactOnPromptTooLong"](
        messages,
        cache_safe_params,
        {"customInstructions": merged_instructions, "trigger": "manual"},
    )

    if not outcome.get("ok"):
        reason = outcome.get("reason", "error")
        if reason == "too_few_groups":
            return {"type": "text", "value": "Not enough messages to compact"}
        if reason == "aborted":
            return {"type": "text", "value": "Compaction canceled."}
        return {"type": "text", "value": "Unable to complete compaction"}

    combined_message = (
        "\n".join(
            filter(
                None,
                [
                    hook_result.get("userDisplayMessage"),
                    outcome.get("result", {}).get("userDisplayMessage"),
                ],
            )
        )
        or None
    )

    return {
        "type": "compact",
        "compactionResult": {
            **outcome.get("result", {}),
            "userDisplayMessage": combined_message,
        },
        "displayText": build_display_text(context, combined_message),
    }


def merge_hook_instructions(custom: str, hook_instructions: str | None) -> str:
    """Merge user-provided custom instructions with hook instructions."""
    if not hook_instructions:
        return custom
    if not custom:
        return hook_instructions
    return f"{custom}\n{hook_instructions}"


def build_display_text(
    context: dict[str, Any], user_display_message: str | None = None
) -> str:
    """Build the display text shown after compaction."""
    parts = ["Compacted"]
    verbose = context.get("options", {}).get("verbose", False)
    if not verbose:
        parts.append("(ctrl+o to see full summary)")
    if user_display_message:
        parts.append(user_display_message)
    upgrade_message = None
    get_upgrade_message = context.get("get_upgrade_message")
    if get_upgrade_message:
        upgrade_message = get_upgrade_message("tip")
    if upgrade_message:
        parts.append(upgrade_message)
    return "\n".join(parts)


async def _get_cache_sharing_params(
    context: dict[str, Any], fork_context_messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build cache-safe parameters for the compact model."""
    get_app_state = context.get("get_app_state")
    get_system_prompt = context.get("get_system_prompt")
    get_user_context = context.get("get_user_context")
    get_system_context = context.get("get_system_context")
    build_effective_system_prompt = context.get("build_effective_system_prompt")

    app_state = get_app_state() if get_app_state else {}
    options = context.get("options", {})

    additional_dirs = []
    if app_state.get("toolPermissionContext", {}).get("additionalWorkingDirectories"):
        additional_dirs = list(
            app_state["toolPermissionContext"]["additionalWorkingDirectories"].keys()
        )

    default_sys_prompt = ""
    if get_system_prompt:
        default_sys_prompt = await get_system_prompt(
            options.get("tools", []),
            options.get("mainLoopModel", ""),
            additional_dirs,
            options.get("mcpClients", []),
        )

    system_prompt = default_sys_prompt
    if build_effective_system_prompt:
        system_prompt = build_effective_system_prompt(
            {
                "mainThreadAgentDefinition": None,
                "toolUseContext": context,
                "customSystemPrompt": options.get("customSystemPrompt"),
                "defaultSystemPrompt": default_sys_prompt,
                "appendSystemPrompt": options.get("appendSystemPrompt"),
            }
        )

    user_context_data = {}
    system_context_data = {}
    if get_user_context:
        user_context_data = await get_user_context()
    if get_system_context:
        system_context_data = await get_system_context()

    return {
        "systemPrompt": system_prompt,
        "userContext": user_context_data,
        "systemContext": system_context_data,
        "toolUseContext": context,
        "forkContextMessages": fork_context_messages,
    }
