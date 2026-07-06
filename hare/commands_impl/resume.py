"""
/resume command - resume a previous conversation session.

Port of: src/commands/resume/resume.tsx + index.ts

Resume a previous session by:
  1. UUID lookup (direct session id match)
  2. Custom title search (exact match)
  3. Interactive session picker (no arg provided)
  4. Cross-project resume detection
"""

from __future__ import annotations

import uuid as uuid_module
from typing import Any

COMMAND_NAME = "resume"
DESCRIPTION = "Resume a previous conversation session"
ALIASES: list[str] = []


def _is_valid_uuid(s: str) -> bool:
    try:
        uuid_module.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Resume a session by UUID, title, or interactive picker."""
    arg = (args or "").strip()

    get_session_id = context.get("get_session_id")
    get_original_cwd = context.get("get_original_cwd")
    load_same_repo_message_logs = context.get("load_same_repo_message_logs")
    load_all_projects_message_logs = context.get("load_all_projects_message_logs")
    load_full_log = context.get("load_full_log")
    get_last_session_log = context.get("get_last_session_log")
    search_sessions_by_custom_title = context.get("search_sessions_by_custom_title")
    is_custom_title_enabled = context.get("is_custom_title_enabled")
    is_lite_log = context.get("is_lite_log")
    get_worktree_paths = context.get("get_worktree_paths")
    check_cross_project_resume = context.get("check_cross_project_resume")
    resume_fn = context.get("resume")
    get_session_id_from_log = context.get("get_session_id_from_log")
    filter_resumable_sessions = context.get("filter_resumable_sessions")

    current_session_id = get_session_id() if get_session_id else ""

    # No argument - list available sessions
    if not arg:
        return {
            "type": "text",
            "value": (
                "Usage: /resume <session-id | session-title>\n\n"
                "Use `/resume` with a session ID or title to resume.\n"
                "Run without arguments to list available sessions (headless mode)."
            ),
            "display": "system",
        }

    # Get worktree paths
    worktree_paths = []
    if get_worktree_paths and get_original_cwd:
        worktree_paths = await get_worktree_paths(get_original_cwd())

    # Load logs
    logs = []
    if load_same_repo_message_logs:
        logs = await load_same_repo_message_logs(worktree_paths)

    if not logs:
        return {"type": "text", "value": "No conversations found to resume."}

    # Filter out current session and sidechains
    if filter_resumable_sessions:
        logs = filter_resumable_sessions(logs, current_session_id)
    else:
        logs = [log for log in logs if not log.get("isSidechain")]

    if not logs:
        return {"type": "text", "value": "No conversations found to resume."}

    # 1. Try UUID match
    if _is_valid_uuid(arg):
        matching = (
            [log for log in logs if get_session_id_from_log(log) == arg]
            if get_session_id_from_log
            else []
        )
        if not matching:
            # Try direct file lookup
            if get_last_session_log:
                direct = await get_last_session_log(arg)
                if direct:
                    matching = [direct]

        if matching:
            log = matching[0]
            full_log = (
                await load_full_log(log) if (is_lite_log and is_lite_log(log)) else log
            )

            # Check cross-project resume
            if check_cross_project_resume:
                cross_check = check_cross_project_resume(
                    full_log, False, worktree_paths
                )
                if cross_check.get("isCrossProject") and not cross_check.get(
                    "isSameRepoWorktree"
                ):
                    return {
                        "type": "text",
                        "value": (
                            f"This conversation is from a different directory.\n\n"
                            f"To resume, run:\n  {cross_check['command']}"
                        ),
                        "display": "user",
                    }

            if resume_fn:
                await resume_fn(arg, full_log, "slash_command_session_id")
                return {
                    "type": "text",
                    "value": f"Resumed session {arg}.",
                    "display": "system",
                }
            return {"type": "text", "value": f"To resume: claude -r {arg}"}

        return {"type": "text", "value": f"Session {arg} was not found."}

    # 2. Try custom title match
    if (
        is_custom_title_enabled
        and is_custom_title_enabled()
        and search_sessions_by_custom_title
    ):
        title_matches = await search_sessions_by_custom_title(arg, exact=True)
        if len(title_matches) == 1:
            log = title_matches[0]
            session_id = get_session_id_from_log(log) if get_session_id_from_log else ""
            if session_id:
                full_log = (
                    await load_full_log(log)
                    if (is_lite_log and is_lite_log(log))
                    else log
                )
                if resume_fn:
                    await resume_fn(session_id, full_log, "slash_command_title")
                    return {
                        "type": "text",
                        "value": f"Resumed session '{arg}'.",
                        "display": "system",
                    }
                return {"type": "text", "value": f"To resume: claude -r {session_id}"}
        elif len(title_matches) > 1:
            return {
                "type": "text",
                "value": f"Found {len(title_matches)} sessions matching '{arg}'. Please use /resume with a specific session ID.",
            }

    return {"type": "text", "value": f"Session '{arg}' was not found."}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[session-id | session-title]",
        "call": call,
    }
