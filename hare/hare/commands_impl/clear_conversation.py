"""
Clear conversation transcript — session lifecycle cleanup.

Port of: src/commands/clear/conversation.ts (251 lines)

Handles: SessionEnd hooks, message clearing, session ID regeneration,
cache clearing, file state reset, SessionStart hooks.
"""

from __future__ import annotations

from typing import Any


async def clear_conversation(
    get_app_state: Any = None,
    set_app_state: Any = None,
    set_messages: Any = None,
    execute_session_end_hooks: Any = None,
    process_session_start_hooks: Any = None,
    clear_session_caches: Any = None,
    regenerate_session_id: Any = None,
    read_file_state: Any = None,
    discovered_skill_names: Any = None,
    loaded_nested_memory_paths: Any = None,
    get_session_end_hook_timeout_ms: Any = None,
    clear_all_plan_slugs: Any = None,
    evict_task_output: Any = None,
    init_task_output_as_symlink: Any = None,
    get_original_cwd: Any = None,
    set_cwd: Any = None,
    save_worktree_state: Any = None,
    get_current_worktree_session: Any = None,
) -> dict[str, Any]:
    """Clear the current conversation while preserving background tasks.

    Returns metadata about preserved tasks.
    """
    # 1. Execute SessionEnd hooks
    if execute_session_end_hooks:
        timeout = (
            get_session_end_hook_timeout_ms()
            if get_session_end_hook_timeout_ms
            else 1500
        )
        await execute_session_end_hooks("clear", timeout)

    # 2. Save preserved background task IDs
    preserved_agent_ids: set[str] = set()
    preserved_local_agents: list[dict[str, Any]] = []
    if get_app_state:
        app_state = get_app_state()
        tasks = (
            app_state.tasks
            if hasattr(app_state, "tasks")
            else app_state.get("tasks", {})
        )
        for task_id, task in (tasks or {}).items():
            is_bg = (
                task.is_backgrounded
                if hasattr(task, "is_backgrounded")
                else task.get("isBackgrounded", False)
            )
            if not is_bg:
                continue
            agent_id_val = (
                task.agent_id if hasattr(task, "agent_id") else task.get("agentId")
            )
            if agent_id_val:
                preserved_agent_ids.add(agent_id_val)
            preserved_local_agents.append(
                {
                    "id": task.id if hasattr(task, "id") else task.get("id", task_id),
                    "agentId": agent_id_val,
                    "status": task.status
                    if hasattr(task, "status")
                    else task.get("status", "running"),
                }
            )

    # 3. Clear messages
    if set_messages:
        set_messages([])

    # 4. Regenerate session ID
    old_session_id = ""
    if regenerate_session_id:
        old_session_id = regenerate_session_id(set_current_as_parent=True)

    # 5. Clear session caches
    if clear_session_caches:
        clear_session_caches(preserved_agent_ids)

    # 6. Reset file state
    if set_cwd and get_original_cwd:
        set_cwd(get_original_cwd())
    if read_file_state and hasattr(read_file_state, "clear"):
        read_file_state.clear()
    if discovered_skill_names is not None and hasattr(discovered_skill_names, "clear"):
        discovered_skill_names.clear()
    if loaded_nested_memory_paths is not None and hasattr(
        loaded_nested_memory_paths, "clear"
    ):
        loaded_nested_memory_paths.clear()

    # 7. Reset plan slugs
    if clear_all_plan_slugs:
        clear_all_plan_slugs()

    # 8. Re-point task output symlinks for preserved agents
    if init_task_output_as_symlink:
        for agent in preserved_local_agents:
            if agent.get("status") == "running":
                await init_task_output_as_symlink(agent["id"], agent["agentId"])

    # 9. Save worktree state
    worktree = get_current_worktree_session() if get_current_worktree_session else None
    if worktree and save_worktree_state:
        save_worktree_state(worktree)

    # 10. SessionStart hooks
    hook_messages = []
    if process_session_start_hooks:
        hook_messages = await process_session_start_hooks("clear")
    if hook_messages and set_messages:
        set_messages(hook_messages)

    return {
        "cleared": True,
        "old_session_id": old_session_id,
        "preserved_agent_ids": list(preserved_agent_ids),
        "hook_messages_count": len(hook_messages),
    }
