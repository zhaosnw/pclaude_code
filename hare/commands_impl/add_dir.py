"""
/add-dir command - add a working directory with permission management.

Port of: src/commands/add-dir/add-dir.tsx + validation.ts + index.ts

Adds a directory to the workspace with:
  - Path validation (exists, is directory, not already in working set)
  - Permission context update via applyPermissionUpdate
  - Sandbox config refresh for Bash access
  - Optional persistence to local settings (--remember flag)
  - Additional directories for CLAUDE.md bootstrap state update
"""

from __future__ import annotations

import os
from typing import Any

COMMAND_NAME = "add-dir"
DESCRIPTION = "Add a working directory to the session"
ALIASES: list[str] = []


async def validate_directory_for_workspace(
    directory_path: str,
    permission_context: dict[str, Any],
) -> dict[str, Any]:
    """Validate that a directory can be added to the workspace.

    Returns a result object with resultType:
      - 'success': path is valid and can be added
      - 'emptyPath': no path provided
      - 'pathNotFound': path doesn't exist
      - 'notADirectory': path exists but isn't a directory
      - 'alreadyInWorkingDirectory': path is already within a working dir
    """
    if not directory_path:
        return {"resultType": "emptyPath"}

    # Resolve absolute path
    absolute_path = os.path.realpath(os.path.expanduser(directory_path))

    # Check if path exists and is a directory
    try:
        if not os.path.exists(absolute_path):
            return {
                "resultType": "pathNotFound",
                "directoryPath": directory_path,
                "absolutePath": absolute_path,
            }
        if not os.path.isdir(absolute_path):
            return {
                "resultType": "notADirectory",
                "directoryPath": directory_path,
                "absolutePath": absolute_path,
            }
    except (PermissionError, OSError):
        return {
            "resultType": "pathNotFound",
            "directoryPath": directory_path,
            "absolutePath": absolute_path,
        }

    # Get all working directories from permission context
    current_working_dirs = _all_working_directories(permission_context)

    # Check if already within an existing working directory
    for working_dir in current_working_dirs:
        if _path_in_working_path(absolute_path, working_dir):
            return {
                "resultType": "alreadyInWorkingDirectory",
                "directoryPath": directory_path,
                "workingDir": working_dir,
            }

    return {"resultType": "success", "absolutePath": absolute_path}


def _all_working_directories(permission_context: dict[str, Any]) -> list[str]:
    """Get all working directories from the permission context."""
    dirs = []
    cwd = permission_context.get("cwd", os.getcwd())
    if cwd:
        dirs.append(cwd)
    additional = permission_context.get("additionalWorkingDirectories", {})
    if isinstance(additional, dict):
        dirs.extend(additional.keys())
    elif isinstance(additional, list):
        dirs.extend(additional)
    return dirs


def _path_in_working_path(path: str, working_dir: str) -> bool:
    """Check if a path is within a working directory."""
    try:
        rel = os.path.relpath(path, working_dir)
        return not rel.startswith("..") and not os.path.isabs(rel)
    except ValueError:
        return False


def add_dir_help_message(result: dict[str, Any]) -> str:
    """Generate help message from validation result."""
    rt = result.get("resultType")
    if rt == "emptyPath":
        return "Please provide a directory path."
    elif rt == "pathNotFound":
        return f"Path {result.get('absolutePath', '')} was not found."
    elif rt == "notADirectory":
        parent_dir = os.path.dirname(result.get("absolutePath", ""))
        return (
            f"{result.get('directoryPath', '')} is not a directory. "
            f"Did you mean to add the parent directory {parent_dir}?"
        )
    elif rt == "alreadyInWorkingDirectory":
        return (
            f"{result.get('directoryPath', '')} is already accessible within "
            f"the existing working directory {result.get('workingDir', '')}."
        )
    elif rt == "success":
        return f"Added {result.get('absolutePath', '')} as a working directory."
    return "Unknown validation result."


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Execute the /add-dir command.

    Handles two flows:
      - With path: validates and adds directly
      - Without path: prompts for directory selection
    """
    directory_path = (args or "").strip()
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")
    apply_permission_update = context.get("apply_permission_update")
    persist_permission_update = context.get("persist_permission_update")
    get_additional_directories = context.get("get_additional_directories_for_claude_md")
    set_additional_directories = context.get("set_additional_directories_for_claude_md")
    sandbox_manager = context.get("sandbox_manager")

    # Check for --remember flag
    remember = False
    if directory_path.endswith(" --remember"):
        directory_path = directory_path[: -len(" --remember")].strip()
        remember = True

    app_state = get_app_state() if get_app_state else {}
    permission_context = app_state.get("toolPermissionContext", {})

    # No path provided - need interactive input (headless: return help)
    if not directory_path:
        return {
            "type": "text",
            "value": "Usage: /add-dir <path> [--remember]",
            "display": "system",
        }

    result = await validate_directory_for_workspace(directory_path, permission_context)
    if result.get("resultType") != "success":
        message = add_dir_help_message(result)
        return {"type": "text", "value": message}

    absolute_path = result["absolutePath"]
    destination = "localSettings" if remember else "session"
    permission_update = {
        "type": "addDirectories",
        "directories": [absolute_path],
        "destination": destination,
    }

    # Apply to session context
    if apply_permission_update and set_app_state:
        latest_app_state = get_app_state() if get_app_state else {}
        updated_context = apply_permission_update(
            latest_app_state.get("toolPermissionContext", {}),
            permission_update,
        )

        def _update(prev: dict[str, Any]) -> dict[str, Any]:
            return {**prev, "toolPermissionContext": updated_context}

        set_app_state(_update)

    # Update sandbox config
    if get_additional_directories and set_additional_directories:
        current_dirs = get_additional_directories()
        if absolute_path not in current_dirs:
            set_additional_directories(current_dirs + [absolute_path])
    if sandbox_manager and hasattr(sandbox_manager, "refresh_config"):
        sandbox_manager.refresh_config()

    # Build response message
    if remember and persist_permission_update:
        try:
            persist_permission_update(permission_update)
            message = f"Added {absolute_path} as a working directory and saved to local settings"
        except Exception as e:
            message = f"Added {absolute_path} as a working directory. Failed to save to local settings: {e}"
    else:
        message = f"Added {absolute_path} as a working directory for this session"

    message_with_hint = f"{message}  · /permissions to manage"
    return {"type": "text", "value": message_with_hint}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "<path> [--remember]",
        "call": call,
    }
