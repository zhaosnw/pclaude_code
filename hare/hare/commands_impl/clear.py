"""
/clear command - clear conversation history.

Port of: src/commands/clear/index.ts + clear.ts + conversation.ts + caches.ts
"""

from __future__ import annotations

import os
import uuid
from typing import Any

COMMAND_NAME = "clear"
DESCRIPTION = "Clear conversation history and free up context"
ALIASES = ["reset", "new"]


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Clear the conversation.

    Executes SessionEnd hooks, clears messages, regenerates session ID,
    clears session caches, resets file pointer, runs SessionStart hooks.
    """
    set_messages = context.get("set_messages")
    read_file_state = context.get("read_file_state")
    discovered_skill_names = context.get("discovered_skill_names")
    loaded_nested_memory_paths = context.get("loaded_nested_memory_paths")
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")
    set_conversation_id = context.get("set_conversation_id")
    execute_session_end_hooks = context.get("execute_session_end_hooks")
    process_session_start_hooks = context.get("process_session_start_hooks")
    clear_session_caches_fn = context.get("clear_session_caches")
    regenerate_session_id_fn = context.get("regenerate_session_id")
    reset_session_file_pointer_fn = context.get("reset_session_file_pointer")
    clear_session_metadata_fn = context.get("clear_session_metadata")
    clear_all_plan_slugs_fn = context.get("clear_all_plan_slugs")
    evict_task_output_fn = context.get("evict_task_output")
    init_task_output_as_symlink_fn = context.get("init_task_output_as_symlink")
    get_session_end_hook_timeout_ms = context.get("get_session_end_hook_timeout_ms")
    get_original_cwd = context.get("get_original_cwd")
    set_cwd_fn = context.get("set_cwd")
    get_current_worktree_session = context.get("get_current_worktree_session")
    save_worktree_state_fn = context.get("save_worktree_state")

    # 1. Execute SessionEnd hooks before clearing
    if execute_session_end_hooks:
        timeout_ms = (
            get_session_end_hook_timeout_ms()
            if get_session_end_hook_timeout_ms
            else 1500
        )
        await execute_session_end_hooks("clear", timeout_ms)

    # 2. Compute preserved tasks (background tasks survive the clear)
    preserved_agent_ids: set[str] = set()
    preserved_local_agents: list[dict[str, Any]] = []
    if get_app_state:
        app_state = get_app_state()
        for task_id, task in app_state.get("tasks", {}).items():
            if task.get("isBackgrounded") is False:
                continue
            if task.get("agentId"):
                preserved_agent_ids.add(task["agentId"])
            if task.get("agentId") and task.get("id"):
                preserved_local_agents.append(task)

    # 3. Clear messages
    if set_messages:
        set_messages([])

    # 4. Force logo re-render by updating conversationId
    if set_conversation_id:
        set_conversation_id(str(uuid.uuid4()))

    # 5. Clear all session-related caches
    if clear_session_caches_fn:
        clear_session_caches_fn(preserved_agent_ids)

    # 6. Reset working directory and file state
    if set_cwd_fn and get_original_cwd:
        set_cwd_fn(get_original_cwd())
    if read_file_state and hasattr(read_file_state, "clear"):
        read_file_state.clear()
    if discovered_skill_names is not None:
        discovered_skill_names.clear()
    if loaded_nested_memory_paths is not None:
        loaded_nested_memory_paths.clear()

    # 7. Clean app state - preserve background tasks, reset everything else
    if set_app_state and get_app_state:

        def _reset_app_state(prev: dict[str, Any]) -> dict[str, Any]:
            next_tasks: dict[str, Any] = {}
            for task_id, task in prev.get("tasks", {}).items():
                if task.get("isBackgrounded") is False:
                    continue
                next_tasks[task_id] = task
            return {
                **prev,
                "tasks": next_tasks,
                "attribution": {},
                "standaloneAgentContext": None,
                "fileHistory": {
                    "snapshots": [],
                    "trackedFiles": [],
                    "snapshotSequence": 0,
                },
                "mcp": {
                    "clients": [],
                    "tools": [],
                    "commands": [],
                    "resources": {},
                    "pluginReconnectKey": prev.get("mcp", {}).get("pluginReconnectKey"),
                },
            }

        set_app_state(_reset_app_state)

    # 8. Clear plan slug cache
    if clear_all_plan_slugs_fn:
        clear_all_plan_slugs_fn()

    # 9. Clear cached session metadata
    if clear_session_metadata_fn:
        clear_session_metadata_fn()

    # 10. Regenerate session ID
    if regenerate_session_id_fn:
        regenerate_session_id_fn(set_current_as_parent=True)
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        os.environ["CLAUDE_CODE_SESSION_ID"] = context.get(
            "get_session_id", lambda: ""
        )()

    # 11. Reset session file pointer
    if reset_session_file_pointer_fn:
        await reset_session_file_pointer_fn()

    # 12. Re-point task output symlinks for preserved agents
    if init_task_output_as_symlink_fn:
        for task in preserved_local_agents:
            if task.get("status") != "running":
                continue
            await init_task_output_as_symlink_fn(task["id"], task["agentId"])

    # 13. Re-persist mode and worktree state
    worktree_session = (
        get_current_worktree_session() if get_current_worktree_session else None
    )
    if worktree_session and save_worktree_state_fn:
        save_worktree_state_fn(worktree_session)

    # 14. Execute SessionStart hooks
    hook_messages = []
    if process_session_start_hooks:
        hook_messages = await process_session_start_hooks("clear")

    if hook_messages:
        if set_messages:
            set_messages(hook_messages)

    return {"type": "clear", "value": ""}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "supportsNonInteractive": False,
        "call": call,
    }
