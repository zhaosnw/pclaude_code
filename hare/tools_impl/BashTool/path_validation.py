"""
Path validation for bash commands.

Port of: src/tools/BashTool/pathValidation.ts

Validates file paths referenced in commands to ensure they don't
escape the working directory or access sensitive locations.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional, Sequence

SENSITIVE_PATHS = frozenset(
    {
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
        "/etc/ssh",
        "/root/.ssh",
        "~/.ssh",
        "/etc/hosts",
        "/etc/resolv.conf",
    }
)

SENSITIVE_DIRECTORIES = frozenset(
    {
        "/",
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/var",
        "/tmp",
        "/root",
        "/boot",
        "/dev",
        "/proc",
        "/sys",
    }
)


def check_path_constraints(
    command: str,
    *,
    working_directory: str = "",
    allowed_directories: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """
    Check if paths in a command are within allowed boundaries.

    Returns dict with 'safe' bool and optional 'reason' and 'paths'.
    """
    paths = extract_paths_from_command(command)
    if not paths:
        return {"safe": True, "paths": []}

    violations = []
    for path in paths:
        resolved = _resolve_path(path, working_directory)

        # Check sensitive paths
        if resolved in SENSITIVE_PATHS or any(
            resolved.startswith(s + "/") for s in SENSITIVE_PATHS
        ):
            violations.append(f"Accesses sensitive path: {path}")
            continue

        # Check if path escapes working directory
        if working_directory and allowed_directories is not None:
            all_allowed = [working_directory] + list(allowed_directories)
            if not any(resolved.startswith(d) for d in all_allowed):
                violations.append(f"Path {path} is outside allowed directories")

    if violations:
        return {"safe": False, "reason": "; ".join(violations), "paths": paths}
    return {"safe": True, "paths": paths}


_QUOTED_SEGMENT_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
# An output redirection: optional fd digits, > or >>, optional >| clobber,
# then a target word. `2>&1`-style fd duplication has no target word (the
# char class excludes &) and is skipped.
_OUTPUT_REDIRECT_RE = re.compile(r"(?:^|[\s;|&])(?:\d+)?>{1,2}\|?\s*([^\s;|&<>]+)")


def extract_output_redirections(command: str) -> list[str]:
    """Return output-redirection target words (`>` / `>>`) in a command.

    Port of extractOutputRedirections (utils/bash/commands.ts), reduced to
    target extraction: quoted segments are masked so operators inside quotes
    are not treated as redirections.
    """
    masked = _QUOTED_SEGMENT_RE.sub(lambda m: " " * len(m.group(0)), command)
    targets = []
    for m in _OUTPUT_REDIRECT_RE.finditer(masked):
        start, end = m.span(1)
        targets.append(command[start:end])
    return targets


def validate_output_redirections(
    command: str,
    permission_context: Any,
    cwd: str,
) -> Optional[Any]:
    """Gate output-redirection write targets behind edit permission.

    Port of validateOutputRedirections (tools/BashTool/pathValidation.ts) +
    isPathAllowed step 3 (utils/permissions/pathValidation.ts:198): `>` and
    `>>` are create operations, so even a target inside the working directory
    is only auto-allowed in acceptEdits mode; otherwise the decision is `ask`
    (which headless print mode converts into a recorded denial). `/dev/null`
    is always safe. Simplification vs TS: path-content allow rules
    (Edit(path)-style) are not consulted because hare does not support them
    yet; that fallthrough ends in `ask` upstream as well.
    """
    mode = getattr(permission_context, "mode", "default")
    if mode == "bypassPermissions":
        return None
    targets = extract_output_redirections(command)
    if not targets:
        return None

    from hare.app_types.permissions import PermissionAskDecision

    base_cwd = os.path.realpath(cwd or os.getcwd())
    working_dirs = [base_cwd]
    additional = getattr(permission_context, "additional_working_directories", None) or {}
    working_dirs += [os.path.realpath(p) for p in additional]

    for target in targets:
        clean = target.strip("'\"")
        resolved = os.path.realpath(_resolve_path(clean, base_cwd))
        if resolved == "/dev/null":
            continue
        in_working_dir = any(
            resolved == d or resolved.startswith(d + os.sep) for d in working_dirs
        )
        if in_working_dir and mode == "acceptEdits":
            continue
        dir_list = ", ".join(f"'{d}'" for d in working_dirs)
        return PermissionAskDecision(
            behavior="ask",
            message=(
                f"Output redirection to '{resolved}' was blocked. For "
                f"security, Hare may only write to files in the allowed "
                f"working directories for this session: {dir_list}."
            ),
        )
    return None


def extract_paths_from_command(command: str) -> list[str]:
    """Extract file paths from a command string."""
    paths = []
    # Match quoted paths
    for m in re.finditer(r'["\']([^"\']+)["\']', command):
        candidate = m.group(1)
        if "/" in candidate or "\\" in candidate:
            paths.append(candidate)

    # Match unquoted absolute paths
    for m in re.finditer(r"(?<!\w)(/[\w./-]+)", command):
        paths.append(m.group(1))

    # Match ~ paths
    for m in re.finditer(r"(?<!\w)(~[\w./-]*)", command):
        paths.append(m.group(1))

    return paths


def _resolve_path(path: str, working_directory: str) -> str:
    """Resolve a path relative to the working directory."""
    if path.startswith("~"):
        path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(working_directory or os.getcwd(), path)
    return os.path.normpath(path)
