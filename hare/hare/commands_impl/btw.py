"""
/btw command - ask a quick side question without interrupting the main conversation.

Port of: src/commands/btw/index.ts + btw.tsx

In the TS CLI this renders a React component that runs a side-question model call
with cache-safe parameters and displays the answer inline. The python SDK sends
the question as a side request with cache-safe parameter construction.

Architecture:
  1. Validates the question is non-empty
  2. Builds CacheSafeParams (preferring getLastCacheSafeParams for prompt cache hits)
  3. Calls runSideQuestion() to get the answer
  4. Returns the response text
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "btw"
DESCRIPTION = "Ask a quick side question without interrupting the main conversation"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Execute a side question.

    The question is processed asynchronously with its own abort controller.
    Response is returned inline.
    """
    question = (args or "").strip()
    if not question:
        return {
            "type": "text",
            "value": "Usage: /btw <your question>",
            "display": "system",
        }

    # Track usage count
    save_global_config = context.get("save_global_config")
    if save_global_config:

        def _increment_btw(config: dict[str, Any]) -> dict[str, Any]:
            return {**config, "btwUseCount": config.get("btwUseCount", 0) + 1}

        save_global_config(_increment_btw)

    # Build cache-safe parameters
    run_side_question = context.get("run_side_question")
    if not run_side_question:
        return {
            "type": "text",
            "value": "Side question service is not available.",
            "display": "system",
        }

    messages = context.get("messages", [])

    # Strip in-progress assistant message (if the last message is incomplete)
    messages = _strip_in_progress_assistant_message(messages)

    # Get messages after compact boundary
    get_messages_after_compact_boundary = context.get(
        "get_messages_after_compact_boundary"
    )
    if get_messages_after_compact_boundary:
        messages = get_messages_after_compact_boundary(messages)

    cache_safe_params = await _build_cache_safe_params(context, messages)

    # Create abort controller for this side question
    abort_controller = context.get("create_abort_controller")
    if abort_controller:
        abort_controller = abort_controller()

    try:
        result = await run_side_question(
            {
                "question": question,
                "cacheSafeParams": cache_safe_params,
            }
        )

        if result.get("response"):
            return {
                "type": "text",
                "value": result["response"],
            }
        return {
            "type": "text",
            "value": "No response received",
            "display": "system",
        }
    except Exception as e:
        return {
            "type": "text",
            "value": f"Failed to get response: {e}",
            "display": "system",
        }
    finally:
        if abort_controller and hasattr(abort_controller, "abort"):
            abort_controller.abort()


def _strip_in_progress_assistant_message(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove the last message if it's an incomplete assistant message."""
    if not messages:
        return messages
    last = messages[-1]
    if (
        last.get("type") == "assistant"
        and last.get("message", {}).get("stop_reason") is None
    ):
        return messages[:-1]
    return messages


async def _build_cache_safe_params(
    context: dict[str, Any], messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build CacheSafeParams for the side question fork.

    Prefers getLastCacheSafeParams for prompt cache hits.
    Falls back to rebuilding from scratch.
    """
    get_last_cache_safe_params = context.get("get_last_cache_safe_params")
    if get_last_cache_safe_params:
        saved = get_last_cache_safe_params()
        if saved:
            return {
                "systemPrompt": saved.get("systemPrompt"),
                "userContext": saved.get("userContext"),
                "systemContext": saved.get("systemContext"),
                "toolUseContext": context,
                "forkContextMessages": messages,
            }

    # Fallback: rebuild from scratch
    get_system_prompt = context.get("get_system_prompt")
    get_user_context = context.get("get_user_context")
    get_system_context = context.get("get_system_context")

    options = context.get("options", {})
    raw_system_prompt = ""
    if get_system_prompt:
        raw_system_prompt = await get_system_prompt(
            options.get("tools", []),
            options.get("mainLoopModel", ""),
            [],
            options.get("mcpClients", []),
        )

    user_context_data = {}
    system_context_data = {}
    if get_user_context:
        user_context_data = await get_user_context()
    if get_system_context:
        system_context_data = await get_system_context()

    return {
        "systemPrompt": raw_system_prompt,
        "userContext": user_context_data,
        "systemContext": system_context_data,
        "toolUseContext": context,
        "forkContextMessages": messages,
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "<question>",
        "immediate": True,
        "call": call,
    }
