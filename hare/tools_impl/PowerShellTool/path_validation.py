"""Path validation for PowerShell tool cwd / targets. Port of: src/tools/PowerShellTool/pathValidation.ts"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

SENSITIVE_PATHS = frozenset(
    {
        "C:\\Windows\\System32\\config\\SAM",
        "C:\\Windows\\System32\\config\\SECURITY",
        "C:\\Windows\\System32\\config\\SYSTEM",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "C:\\ProgramData\\Microsoft\\Crypto",
    }
)

SENSITIVE_DIRECTORIES = frozenset(
    {
        "C:\\Windows",
        "C:\\Windows\\System32",
        "C:\\Windows\\SysWOW64",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "C:\\ProgramData",
        "C:\\Boot",
        "C:\\$Recycle.Bin",
    }
)

_WINDOWS_DRIVE_REGEX = re.compile(r"^[A-Za-z]:[/\\]")
_UNC_PATH_REGEX = re.compile(r"^\\\\[^\\]+\\[^\\]+")
_PS_VARIABLE_REGEX = re.compile(r"\$env:[A-Za-z_][A-Za-z0-9_]*")
_GLOB_PATTERN_REGEX = re.compile(r"[*?\[\]{}]")


@dataclass
class PathValidationResult:
    safe: bool
    paths: list[str]
    reason: Optional[str] = None


def is_path_within_workspace(candidate: Path, workspace_root: Path) -> bool:
    """Check whether *candidate* resolves inside *workspace_root*."""
    try:
        candidate.resolve().relative_to(workspace_root.resolve())
        return True
    except ValueError:
        return False


def expand_powershell_path(path: str, working_directory: str = "") -> str:
    """Resolve a PowerShell-style path (including env: variables and ~) to an absolute path."""
    expanded = path.strip()

    # Expand ~ to user home
    if expanded in ("~", "~\\", "~/") or expanded.startswith(("~\\", "~/")):
        expanded = str(Path.home()) + expanded[1:]

    # Resolve $env: variables
    for match in _PS_VARIABLE_REGEX.finditer(expanded):
        var_name = match.group(0)[5:]  # strip "$env:"
        var_value = os.environ.get(var_name, "")
        expanded = expanded.replace(match.group(0), var_value)

    # Determine if already absolute (handles both POSIX / and Windows C:\ on any OS)
    is_abs = os.path.isabs(expanded) or bool(_WINDOWS_DRIVE_REGEX.match(expanded))

    # Normalize mixed slashes to forward slashes for consistent cross-platform matching
    expanded = re.sub(r"[/\\]+", "/", expanded)

    if not is_abs and working_directory:
        expanded = os.path.normpath(os.path.join(working_directory, expanded))
        expanded = expanded.replace("\\", "/")

    return expanded.rstrip("/") or "/"


def extract_paths_from_powershell(command: str) -> list[str]:
    """Extract file-system paths from a PowerShell command string."""
    paths: list[str] = []

    # Quoted paths (single or double quotes)
    for m in re.finditer(r"""['"]([^'"]+)['"]""", command):
        candidate = m.group(1)
        if "/" in candidate or "\\" in candidate or ":" in candidate:
            paths.append(candidate)

    # Unquoted Windows drive paths: C:\..., D:\...
    for m in re.finditer(r"\b([A-Za-z]:[/\\][\w.\-\\/ ]+)", command):
        paths.append(m.group(1))

    # UNC paths: \\server\share\...
    for m in re.finditer(_UNC_PATH_REGEX, command):
        paths.append(m.group(0))

    # $env: variable paths
    for m in _PS_VARIABLE_REGEX.finditer(command):
        paths.append(m.group(0))

    # ~ home-relative paths
    for m in re.finditer(r"(?<!\w)(~[/\\][\w.\-\\/ ]*)", command):
        paths.append(m.group(1))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _norm(path: str) -> str:
    """Normalize a path for comparison: lowercase, forward-slash separator."""
    return path.replace("\\", "/").lower()


# Pre-normalize sensitive sets for cross-platform matching
_NORM_SENSITIVE_PATHS = frozenset(_norm(p) for p in SENSITIVE_PATHS)
_NORM_SENSITIVE_DIRS = frozenset(_norm(d) for d in SENSITIVE_DIRECTORIES)


def check_powershell_path_constraints(
    command: str,
    *,
    working_directory: str = "",
    allowed_directories: Optional[Sequence[str]] = None,
) -> PathValidationResult:
    """Validate that paths referenced in a PowerShell command are within allowed boundaries.

    Returns a ``PathValidationResult`` indicating safety, extracted paths, and
    an optional reason string when unsafe.
    """
    paths = extract_paths_from_powershell(command)
    if not paths:
        return PathValidationResult(safe=True, paths=[])

    violations: list[str] = []
    for path in paths:
        # Flag UNC network paths as requiring manual approval
        if is_unc_path(path):
            violations.append(f"UNC network path requires manual approval: {path}")
            continue

        resolved = _norm(expand_powershell_path(path, working_directory))

        # Block sensitive exact paths
        if resolved in _NORM_SENSITIVE_PATHS:
            violations.append(f"Accesses sensitive path: {path}")
            continue

        # Block children of sensitive directories
        if any(resolved.startswith(d + "/") for d in _NORM_SENSITIVE_DIRS):
            violations.append(f"Accesses sensitive directory: {path}")
            continue

        # Enforce working-directory / allowed-directory containment
        if working_directory and allowed_directories is not None:
            all_allowed = [
                _norm(os.path.normpath(working_directory)),
            ] + [_norm(os.path.normpath(d)) for d in allowed_directories]
            if not any(resolved.startswith(d) for d in all_allowed):
                violations.append(f"Path '{path}' is outside allowed directories")

    if violations:
        return PathValidationResult(
            safe=False, paths=paths, reason="; ".join(violations)
        )
    return PathValidationResult(safe=True, paths=paths)


def is_unc_path(path: str) -> bool:
    """Return True if *path* is a UNC network path."""
    return bool(_UNC_PATH_REGEX.match(path.replace("/", "\\")))


def is_glob_path(path: str) -> bool:
    """Return True if *path* contains glob/splat characters."""
    return bool(_GLOB_PATTERN_REGEX.search(path))
