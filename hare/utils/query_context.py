"""
Cache-key prefix assembly for `query()` / side questions. Port of src/utils/queryContext.ts.
"""

from __future__ import annotations

from typing import Any

from hare.utils.abort_controller import create_abort_controller
from hare.utils.model import get_main_loop_model
from hare.utils.system_prompt_type import as_system_prompt


async def fetch_system_prompt_parts(
    *,
    tools: Any,
    main_loop_model: str,
    additional_working_directories: list[str],
    mcp_clients: list[Any],
    custom_system_prompt: str | None,
) -> dict[str, Any]:
    if custom_system_prompt is not None:
        default_system_prompt: list[str] = []
    else:
        try:
            from hare.constants.prompts import get_system_prompt

            default_system_prompt = await get_system_prompt(
                tools,
                main_loop_model,
                additional_working_directories,
                mcp_clients,
            )
        except ImportError:
            default_system_prompt = []
    try:
        from hare.context import get_system_context, get_user_context

        user_context = get_user_context()
        system_context = (
            {} if custom_system_prompt is not None else get_system_context()
        )
    except ImportError:
        user_context = {}
        system_context = {}
    return {
        "defaultSystemPrompt": default_system_prompt,
        "userContext": user_context,
        "systemContext": system_context,
    }


async def build_side_question_fallback_params(
    *,
    tools: Any,
    commands: list[Any],
    mcp_clients: list[Any],
    messages: list[Any],
    read_file_state: Any,
    get_app_state: Any,
    set_app_state: Any,
    custom_system_prompt: str | None,
    append_system_prompt: str | None,
    thinking_config: Any,
    agents: list[Any],
) -> dict[str, Any]:
    main_loop_model = get_main_loop_model()
    app_state = get_app_state()
    extra_dirs = []
    try:
        tpc = getattr(app_state, "tool_permission_context", None)
        if tpc and hasattr(tpc, "additional_working_directories"):
            extra_dirs = list(tpc.additional_working_directories.keys())
    except Exception:
        pass
    parts = await fetch_system_prompt_parts(
        tools=tools,
        main_loop_model=main_loop_model,
        additional_working_directories=extra_dirs,
        mcp_clients=mcp_clients,
        custom_system_prompt=custom_system_prompt,
    )
    if custom_system_prompt is not None:
        chunks: list[str] = [custom_system_prompt]
    else:
        chunks = list(parts["defaultSystemPrompt"])
    if append_system_prompt:
        chunks.append(append_system_prompt)
    system_prompt = as_system_prompt("\n".join(chunks))
    last = messages[-1] if messages else None
    fork_context_messages = messages
    if last is not None and getattr(last, "type", None) == "assistant":
        msg = getattr(last, "message", None)
        sr = getattr(msg, "stop_reason", None) if msg else None
        if sr is None:
            fork_context_messages = messages[:-1]
    tc = thinking_config or {"type": "disabled"}
    tool_use_context: dict[str, Any] = {
        "options": {
            "commands": commands,
            "debug": False,
            "mainLoopModel": main_loop_model,
            "tools": tools,
            "verbose": False,
            "thinkingConfig": tc,
            "mcpClients": mcp_clients,
            "mcpResources": {},
            "isNonInteractiveSession": True,
            "agentDefinitions": {"activeAgents": agents, "allAgents": []},
            "customSystemPrompt": custom_system_prompt,
            "appendSystemPrompt": append_system_prompt,
        },
        "abortController": create_abort_controller(),
        "readFileState": read_file_state,
        "getAppState": get_app_state,
        "setAppState": set_app_state,
        "messages": fork_context_messages,
        "setInProgressToolUseIDs": lambda *_: None,
        "setResponseLength": lambda *_: None,
        "updateFileHistoryState": lambda *_: None,
        "updateAttributionState": lambda *_: None,
    }
    return {
        "systemPrompt": system_prompt,
        "userContext": parts["userContext"],
        "systemContext": parts["systemContext"],
        "toolUseContext": tool_use_context,
        "forkContextMessages": fork_context_messages,
    }
