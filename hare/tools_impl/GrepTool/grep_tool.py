"""
GrepTool — ripgrep-based code search with context flags, VCS exclusions,
pagination, and mode-specific output formatting.

Port of: src/tools/GrepTool/GrepTool.ts
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Optional

TOOL_NAME = "Grep"
GREP_TOOL_NAME = TOOL_NAME

# Default head limit for content results
DEFAULT_HEAD_LIMIT = 250
# Max column width to truncate minified/base64 content
MAX_COLUMNS = 500

# VCS directories to exclude
VCS_EXCLUDE_GLOBS = [
    "--glob", "!.git",
    "--glob", "!.svn",
    "--glob", "!.hg",
    "--glob", "!.bzr",
    "--glob", "!.jj",
    "--glob", "!.sl",
]


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: cwd)",
            },
            "glob": {
                "type": "string",
                "description": "File glob filter (e.g., '*.py' or '*.{ts,tsx}')",
            },
            "type": {
                "type": "string",
                "description": "File type filter (ripgrep --type)",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode (default: files_with_matches)",
            },
            "head_limit": {
                "type": "integer",
                "description": f"Max results (default: {DEFAULT_HEAD_LIMIT})",
            },
            "offset": {
                "type": "integer",
                "description": "Pagination offset (0-based)",
            },
            "-A": {
                "type": "integer",
                "description": "Lines after match",
            },
            "-B": {
                "type": "integer",
                "description": "Lines before match",
            },
            "-C": {
                "type": "integer",
                "description": "Alias for context.",
            },
            "context": {
                "type": "integer",
                "description": "Number of lines to show before and after each match (like -C). Used in content mode.",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers (default: true in content mode)",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode",
            },
        },
        "required": ["pattern"],
    }


async def call(
    pattern: str,
    path: Optional[str] = None,
    output_mode: str = "files_with_matches",
    head_limit: Optional[int] = None,
    offset: int = 0,
    multiline: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute a ripgrep search with full feature support."""
    base = Path(path).resolve() if path else Path.cwd()
    cwd = str(base)

    # Build ripgrep arguments
    args = ["rg", "--no-heading", "--color", "never"]

    # VCS exclusions
    args.extend(VCS_EXCLUDE_GLOBS)

    # Truncate long lines (minified/base64)
    args.extend(["--max-columns", str(MAX_COLUMNS)])

    # Output mode
    if output_mode == "files_with_matches":
        args.append("-l")
    elif output_mode == "count":
        args.append("-c")

    # Context flags
    after = _parse_int_arg(kwargs, "-A")
    before = _parse_int_arg(kwargs, "-B")
    # 2.1.88 exposes both `context` (canonical) and `-C` (alias); accept either.
    context = _parse_int_arg(kwargs, "-C") or _parse_int_arg(kwargs, "context")
    if after:
        args.extend(["-A", str(after)])
    if before:
        args.extend(["-B", str(before)])
    if context:
        args.extend(["-C", str(context)])

    # Line numbers (default on in content mode, but always explicit)
    show_line_numbers = kwargs.get("-n", True)
    if show_line_numbers and output_mode == "content":
        args.append("-n")

    # Case insensitive
    if kwargs.get("-i", False):
        args.append("-i")

    # Multiline mode
    if multiline:
        args.extend(["-U", "--multiline-dotall"])

    # File type filter
    if kwargs.get("type"):
        args.extend(["--type", str(kwargs["type"])])

    # Glob filter — handle comma-separated globs and brace patterns
    glob_val = kwargs.get("glob")
    if glob_val:
        globs = _split_glob_patterns(str(glob_val))
        for g in globs:
            args.extend(["--glob", g])

    # Pattern with leading dash protection
    if pattern.startswith("-"):
        args.extend(["-e", pattern])
    else:
        args.extend(["--", pattern])

    args.append(cwd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        stderr_output = stderr.decode("utf-8", errors="replace")

        # ripgrep exit codes: 0=matches found, 1=no matches, 2=error
        if proc.returncode == 2 and stderr_output:
            return {"error": f"ripgrep error: {stderr_output.strip()}"}

        lines = output.strip().split("\n") if output.strip() else []

        # Apply head_limit and offset
        effective_limit = head_limit if head_limit is not None else DEFAULT_HEAD_LIMIT
        total_matches = len(lines)
        applied_offset = 0
        applied_limit = 0

        if output_mode in ("files_with_matches", "content"):
            if offset > 0 and offset < len(lines):
                lines = lines[offset:]
                applied_offset = offset
            if len(lines) > effective_limit:
                lines = lines[:effective_limit]
                applied_limit = effective_limit

        # Format output by mode
        formatted = _format_output(
            lines=lines,
            output_mode=output_mode,
            cwd=cwd,
            app_limit=applied_limit,
            app_offset=applied_offset,
            total_matches=total_matches,
        )

        return formatted

    except FileNotFoundError:
        return {"error": "ripgrep (rg) not found in PATH. Install from https://github.com/BurntSushi/ripgrep"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_int_arg(kwargs: dict[str, Any], key: str) -> Optional[int]:
    """Parse a keyword argument as an integer."""
    val = kwargs.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _split_glob_patterns(glob_str: str) -> list[str]:
    """Split comma-separated globs while preserving brace patterns like *.{ts,tsx}."""
    # Preserve brace patterns by temporarily replacing commas inside braces
    brace_pattern = re.compile(r"\{[^}]+\}")
    replacements: dict[str, str] = {}
    counter = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal counter
        key = f"__BRACE_{counter}__"
        replacements[key] = m.group(0)
        counter += 1
        return key

    temp = brace_pattern.sub(_replace, glob_str)
    parts = [p.strip() for p in temp.split(",") if p.strip()]
    # Restore brace patterns
    result = []
    for p in parts:
        for key, val in replacements.items():
            p = p.replace(key, val)
        result.append(p)
    return result


def _relativize_path(abs_path: str, cwd: str) -> str:
    """Convert absolute path to relative (from cwd) if under cwd."""
    try:
        return str(Path(abs_path).relative_to(cwd))
    except ValueError:
        return abs_path


def _format_output(
    lines: list[str],
    output_mode: str,
    cwd: str,
    app_limit: int,
    app_offset: int,
    total_matches: int,
) -> dict[str, Any]:
    """Format ripgrep output by mode, with relativization and pagination."""
    if output_mode == "files_with_matches":
        # Sort by mtime for stable output
        files = [_relativize_path(line, cwd) for line in lines if line.strip()]
        files.sort(key=lambda f: _get_mtime(os.path.join(cwd, f)))
        result = {
            "output": "\n".join(files) if files else "(no matches)",
            "numFiles": len(files),
            "matchCount": len(files),
        }
        if app_limit:
            result["appliedLimit"] = app_limit
        if app_offset:
            result["appliedOffset"] = app_offset
        return result

    elif output_mode == "count":
        total = 0
        file_count = 0
        count_lines: list[str] = []
        for line in lines:
            if ":" in line:
                file_path, count_str = line.rsplit(":", 1)
                file_path = _relativize_path(file_path, cwd)
                try:
                    count = int(count_str.strip())
                    total += count
                    file_count += 1
                except ValueError:
                    pass
                count_lines.append(f"{file_path}:{count_str.strip()}")
            else:
                count_lines.append(line)
        result = {
            "output": "\n".join(count_lines) if count_lines else "(no matches)",
            "totalMatches": total,
            "numFiles": file_count,
        }
        return result

    else:  # content mode
        # Relativize paths in content output
        relativized = [_relativize_path_in_content(line, cwd) for line in lines]
        output_text = "\n".join(relativized) if relativized else "(no matches)"

        footer_parts = []
        if app_limit:
            footer_parts.append(f"limit: {app_limit}")
        if app_offset:
            footer_parts.append(f"offset: {app_offset}")
        if footer_parts:
            output_text += f"\n\n[Showing results with pagination = {', '.join(footer_parts)}]"

        result: dict[str, Any] = {
            "output": output_text,
            "matchCount": total_matches,
        }
        if app_limit:
            result["appliedLimit"] = app_limit
        if app_offset:
            result["appliedOffset"] = app_offset
        return result


def _relativize_path_in_content(line: str, cwd: str) -> str:
    """Relativize the path portion of a ripgrep content line (format: path:line:content)."""
    if not line:
        return line
    # Match: <path>:<line_number>:<content>
    # Use colon careful matching for Windows paths
    sep_idx = line.find(":")
    if sep_idx == -1:
        return line
    path_part = line[:sep_idx]
    rest = line[sep_idx:]
    if os.path.isabs(path_part):
        try:
            rel = str(Path(path_part).relative_to(cwd))
            return rel + rest
        except ValueError:
            pass
    return line


def _get_mtime(filepath: str) -> float:
    """Get file mtime, returning 0 if file doesn't exist."""
    try:
        return os.path.getmtime(filepath)
    except OSError:
        return 0.0
