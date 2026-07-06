"""
CLI setup — environment initialization, session config, preflight checks.

Port of: src/setup.ts (477 lines)

Single entry point for all session startup: CWD, git, worktree, hooks,
plugin loading, auth prefetch, permission safety checks.
"""

from __future__ import annotations

import os
import sys
from typing import Any


async def setup(
    cwd: str,
    permission_mode: str = "default",
    allow_dangerously_skip_permissions: bool = False,
    worktree_enabled: bool = False,
    worktree_name: str | None = None,
    tmux_enabled: bool = False,
    custom_session_id: str | None = None,
    worktree_pr_number: int | None = None,
    *,
    get_session_id: Any = None,
    set_cwd_fn: Any = None,
    set_original_cwd: Any = None,
    set_project_root: Any = None,
    get_project_root: Any = None,
    switch_session: Any = None,
    capture_hooks_config_snapshot: Any = None,
    update_hooks_config_snapshot: Any = None,
    initialize_file_changed_watcher: Any = None,
    init_session_memory: Any = None,
    get_commands: Any = None,
    load_plugin_hooks: Any = None,
    prefetch_api_key: Any = None,
    check_for_release_notes: Any = None,
    get_global_config: Any = None,
    get_recent_activity: Any = None,
    get_current_project_config: Any = None,
    log_event: Any = None,
    log_diag: Any = None,
    get_is_git: Any = None,
    find_git_root: Any = None,
    find_canonical_git_root: Any = None,
    create_worktree_for_session: Any = None,
    save_worktree_state: Any = None,
    clear_memory_file_caches: Any = None,
    get_is_non_interactive: Any = None,
    is_bare_mode: Any = None,
    has_worktree_create_hook: Any = None,
) -> None:
    """Initialize the session environment."""

    # 1. Python version check
    if sys.version_info < (3, 9):
        print("Error: Hare requires Python 3.9 or higher.", file=sys.stderr)
        sys.exit(1)

    # 2. Custom session ID
    if custom_session_id and switch_session:
        switch_session(custom_session_id)

    # 3. Set CWD (must be first)
    if set_cwd_fn:
        set_cwd_fn(cwd)

    # 4. Non-interactive checks
    non_interactive = get_is_non_interactive() if get_is_non_interactive else False
    bare_mode = is_bare_mode() if is_bare_mode else False

    # 5. Hooks config snapshot (after CWD)
    if capture_hooks_config_snapshot:
        capture_hooks_config_snapshot()

    # 6. File changed watcher
    if initialize_file_changed_watcher:
        initialize_file_changed_watcher(cwd)

    # 7. Worktree setup
    if worktree_enabled:
        has_hook = has_worktree_create_hook() if has_worktree_create_hook else False
        in_git = await get_is_git() if get_is_git else False

        if not has_hook and not in_git:
            print(
                f"Error: Can only use --worktree in a git repository, "
                f"but {cwd} is not a git repository.",
                file=sys.stderr,
            )
            sys.exit(1)

        slug = (
            f"pr-{worktree_pr_number}"
            if worktree_pr_number
            else (worktree_name or "session")
        )

        if (
            in_git
            and find_canonical_git_root
            and find_git_root
            and create_worktree_for_session
        ):
            main_root = find_canonical_git_root(cwd)
            if not main_root:
                print(
                    "Error: Could not determine the main git repository root.",
                    file=sys.stderr,
                )
                sys.exit(1)

            local_root = find_git_root(cwd) or cwd
            if main_root != local_root:
                os.chdir(main_root)
                if set_cwd_fn:
                    set_cwd_fn(main_root)

            sid = get_session_id() if get_session_id else ""
            try:
                worktree_session = await create_worktree_for_session(sid, slug)
            except Exception as e:
                print(f"Error creating worktree: {e}", file=sys.stderr)
                sys.exit(1)

            if log_event:
                log_event("tengu_worktree_created", {})

            os.chdir(worktree_session.get("worktreePath", cwd))
            if set_cwd_fn:
                set_cwd_fn(os.getcwd())
            if set_original_cwd:
                set_original_cwd(os.getcwd())
            if set_project_root:
                set_project_root(os.getcwd())
            if save_worktree_state:
                save_worktree_state(worktree_session)
            if clear_memory_file_caches:
                clear_memory_file_caches()
            if update_hooks_config_snapshot:
                update_hooks_config_snapshot()

    # 8. Session memory (skip in bare mode)
    if not bare_mode and init_session_memory:
        init_session_memory()

    # 9. Command loading (skip in bare mode)
    if not bare_mode:
        if get_commands and get_project_root:
            get_commands(get_project_root())

    # 10. Plugin hooks (skip in bare mode)
    if not bare_mode and load_plugin_hooks:
        try:
            await load_plugin_hooks()
        except Exception:
            pass

    # 11. Release notes (skip in bare mode)
    if not bare_mode and get_global_config and check_for_release_notes:
        cfg = get_global_config()
        try:
            rn = await check_for_release_notes(cfg.get("lastReleaseNotesSeen"))
            if rn.get("hasReleaseNotes") and get_recent_activity:
                await get_recent_activity()
        except Exception:
            pass

    # 12. Permission safety check
    if permission_mode == "bypassPermissions" or allow_dangerously_skip_permissions:
        if sys.platform != "win32" and hasattr(os, "getuid") and os.getuid() == 0:
            sandbox = os.environ.get("IS_SANDBOX") == "1"
            if not sandbox and not os.environ.get("CLAUDE_CODE_BUBBLEWRAP"):
                print(
                    "--dangerously-skip-permissions cannot be used with root/sudo privileges",
                    file=sys.stderr,
                )
                sys.exit(1)

    # 13. Auth prefetch
    if prefetch_api_key:
        try:
            await prefetch_api_key(non_interactive)
        except Exception:
            pass

    # 14. Log exit event from last session
    if get_current_project_config and log_event:
        cfg = get_current_project_config()
        if cfg.get("lastCost") is not None:
            log_event(
                "tengu_exit",
                {
                    "last_session_cost": cfg.get("lastCost"),
                    "last_session_api_duration": cfg.get("lastAPIDuration"),
                    "last_session_duration": cfg.get("lastDuration"),
                    "last_session_lines_added": cfg.get("lastLinesAdded"),
                    "last_session_lines_removed": cfg.get("lastLinesRemoved"),
                    "last_session_id": cfg.get("lastSessionId"),
                },
            )
