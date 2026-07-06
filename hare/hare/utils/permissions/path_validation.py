"""
Path validation for permission checks.

Port of: src/utils/permissions/pathValidation.ts
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from hare.app_types.permissions import ToolPermissionContext
from hare.utils.fs_operations import get_fs_implementation, safe_resolve_path
from hare.utils.path_utils import contains_path_traversal
from hare.utils.permissions.filesystem import (
    check_path_safety_for_auto_edit,
    matching_rule_for_input,
    path_in_allowed_working_path,
)
from hare.utils.platform import get_platform

FileOperationType = Literal["read", "write", "create"]

MAX_DIRS_TO_LIST = 5
GLOB_PATTERN_REGEX = re.compile(r"[*?[\]{}]")
WINDOWS_DRIVE_ROOT_REGEX = re.compile(r"^[A-Za-z]:/?$")
WINDOWS_DRIVE_CHILD_REGEX = re.compile(r"^[A-Za-z]:/[^/]+$")


def format_directory_list(directories: list[str]) -> str:
    dir_count = len(directories)
    if dir_count <= MAX_DIRS_TO_LIST:
        return ", ".join(f"'{d}'" for d in directories)
    first = ", ".join(f"'{d}'" for d in directories[:MAX_DIRS_TO_LIST])
    return f"{first}, and {dir_count - MAX_DIRS_TO_LIST} more"


def get_glob_base_directory(path: str) -> str:
    glob_match = GLOB_PATTERN_REGEX.search(path)
    if not glob_match:
        return path
    before_glob = path[: glob_match.start()]
    if get_platform() == "windows":
        last_sep = max(before_glob.rfind("/"), before_glob.rfind("\\"))
    else:
        last_sep = before_glob.rfind("/")
    if last_sep == -1:
        return "."
    return before_glob[:last_sep] or "/"


def expand_tilde(path: str) -> str:
    """Expand ~ at start to user home (~user not supported)."""
    if path == "~" or path.startswith("~/"):
        return str(Path.home()) + path[1:]
    if os.name == "nt" and path.startswith("~\\"):
        return str(Path.home()) + path[1:]
    return path


def contains_vulnerable_unc_path(path: str) -> bool:
    """Stub: block obvious UNC credential-leak patterns (extend as needed)."""
    return path.startswith("\\\\") and "@" in path


def is_path_in_sandbox_write_allowlist(resolved_path: str) -> bool:
    """Stub: sandbox FS write allowlist (see SandboxManager in TS)."""
    return False


@dataclass
class PathCheckResult:
    allowed: bool
    decision_reason: dict[str, Any] | None = None


@dataclass
class ResolvedPathCheckResult:
    allowed: bool
    resolved_path: str
    decision_reason: dict[str, Any] | None = None


def is_path_allowed(
    resolved_path: str,
    context: ToolPermissionContext,
    operation_type: FileOperationType,
    precomputed_paths_to_check: Optional[tuple[str, ...]] = None,
) -> PathCheckResult:
    permission_type = "read" if operation_type == "read" else "edit"

    deny_rule = matching_rule_for_input(resolved_path, context, permission_type, "deny")
    if deny_rule is not None:
        return PathCheckResult(
            allowed=False, decision_reason={"type": "rule", "rule": deny_rule}
        )

    if operation_type != "read":
        safety = check_path_safety_for_auto_edit(
            resolved_path,
            list(precomputed_paths_to_check) if precomputed_paths_to_check else None,
        )
        if not safety.get("safe"):
            return PathCheckResult(
                allowed=False,
                decision_reason={
                    "type": "safetyCheck",
                    "reason": safety.get("message", ""),
                    "classifierApprovable": safety.get("classifierApprovable"),
                },
            )

    is_in_working_dir = path_in_allowed_working_path(
        resolved_path, context, precomputed_paths_to_check
    )
    if is_in_working_dir:
        if operation_type == "read" or context.mode == "acceptEdits":
            return PathCheckResult(allowed=True)

    if operation_type == "read":
        allow_rule = matching_rule_for_input(
            resolved_path, context, permission_type, "allow"
        )
        if allow_rule is not None:
            return PathCheckResult(
                allowed=True, decision_reason={"type": "rule", "rule": allow_rule}
            )
        return PathCheckResult(allowed=False)

    if operation_type != "read" and not is_in_working_dir:
        if is_path_in_sandbox_write_allowlist(resolved_path):
            return PathCheckResult(
                allowed=True,
                decision_reason={
                    "type": "other",
                    "reason": "Path is in sandbox write allowlist",
                },
            )

    allow_rule = matching_rule_for_input(
        resolved_path, context, permission_type, "allow"
    )
    if allow_rule is not None:
        return PathCheckResult(
            allowed=True, decision_reason={"type": "rule", "rule": allow_rule}
        )

    return PathCheckResult(allowed=False)


def validate_glob_pattern(
    clean_path: str,
    cwd: str,
    tool_permission_context: ToolPermissionContext,
    operation_type: FileOperationType,
) -> ResolvedPathCheckResult:
    fs = get_fs_implementation()
    if contains_path_traversal(clean_path):
        absolute_path = (
            clean_path if os.path.isabs(clean_path) else os.path.join(cwd, clean_path)
        )
        resolved_path, is_canonical = safe_resolve_path(fs, absolute_path)
        result = is_path_allowed(
            resolved_path,
            tool_permission_context,
            operation_type,
            (resolved_path,) if is_canonical else None,
        )
        return ResolvedPathCheckResult(
            allowed=result.allowed,
            resolved_path=resolved_path,
            decision_reason=result.decision_reason,
        )

    base_path = get_glob_base_directory(clean_path)
    absolute_base = (
        base_path if os.path.isabs(base_path) else os.path.join(cwd, base_path)
    )
    resolved_path, is_canonical = safe_resolve_path(fs, absolute_base)
    result = is_path_allowed(
        resolved_path,
        tool_permission_context,
        operation_type,
        (resolved_path,) if is_canonical else None,
    )
    return ResolvedPathCheckResult(
        allowed=result.allowed,
        resolved_path=resolved_path,
        decision_reason=result.decision_reason,
    )


def is_dangerous_removal_path(resolved_path: str) -> bool:
    forward_slashed = re.sub(r"[\\/]+", "/", resolved_path)
    if forward_slashed == "*" or forward_slashed.endswith("/*"):
        return True
    normalized_path = (
        forward_slashed if forward_slashed == "/" else forward_slashed.rstrip("/")
    )
    if normalized_path == "/":
        return True
    if WINDOWS_DRIVE_ROOT_REGEX.match(normalized_path):
        return True
    home = str(Path.home()).replace("\\", "/")
    if normalized_path == home:
        return True
    parent_dir = os.path.dirname(normalized_path)
    if parent_dir == "/":
        return True
    if WINDOWS_DRIVE_CHILD_REGEX.match(normalized_path):
        return True
    return False


def validate_path(
    path: str,
    cwd: str,
    tool_permission_context: ToolPermissionContext,
    operation_type: FileOperationType,
) -> ResolvedPathCheckResult:
    clean_path = expand_tilde(re.sub(r"^['\"]|['\"]$", "", path))

    if contains_vulnerable_unc_path(clean_path):
        return ResolvedPathCheckResult(
            allowed=False,
            resolved_path=clean_path,
            decision_reason={
                "type": "other",
                "reason": "UNC network paths require manual approval",
            },
        )

    if clean_path.startswith("~"):
        return ResolvedPathCheckResult(
            allowed=False,
            resolved_path=clean_path,
            decision_reason={
                "type": "other",
                "reason": (
                    "Tilde expansion variants (~user, ~+, ~-) in paths require manual approval"
                ),
            },
        )

    if "$" in clean_path or "%" in clean_path or clean_path.startswith("="):
        return ResolvedPathCheckResult(
            allowed=False,
            resolved_path=clean_path,
            decision_reason={
                "type": "other",
                "reason": "Shell expansion syntax in paths requires manual approval",
            },
        )

    if GLOB_PATTERN_REGEX.search(clean_path):
        if operation_type in ("write", "create"):
            return ResolvedPathCheckResult(
                allowed=False,
                resolved_path=clean_path,
                decision_reason={
                    "type": "other",
                    "reason": (
                        "Glob patterns are not allowed in write operations. "
                        "Please specify an exact file path."
                    ),
                },
            )
        return validate_glob_pattern(
            clean_path, cwd, tool_permission_context, operation_type
        )

    absolute_path = (
        clean_path if os.path.isabs(clean_path) else os.path.join(cwd, clean_path)
    )
    fs = get_fs_implementation()
    resolved_path, is_canonical = safe_resolve_path(fs, absolute_path)
    result = is_path_allowed(
        resolved_path,
        tool_permission_context,
        operation_type,
        (resolved_path,) if is_canonical else None,
    )
    return ResolvedPathCheckResult(
        allowed=result.allowed,
        resolved_path=resolved_path,
        decision_reason=result.decision_reason,
    )
