"""Team memory path checks and operation classification (port of teamMemoryOps.ts).

Provides detection functions that classify tool-use inputs as targeting
team memory files, directories, or patterns. Used by the collapse engine
to route team-memory operations into separate summary counters so the
user sees team-specific language ("recalled 3 team memories") alongside
regular memory operations.

Public API
----------
is_team_mem_file(path)                   – re-exported from team_mem_paths
is_team_memory_search(tool_input)        – Grep/Glob searches targeting team mem
is_team_memory_pattern(pattern)          – glob/regex pattern targeting team mem
is_team_memory_directory(dir_path)       – directory path within team mem
is_team_memory_write_or_edit(name, inp)  – Write/Edit tools targeting team mem
is_team_memory_read(tool_input)          – Read/ReadFile tools targeting team mem
classify_tool_use_for_team_memory(...)   – comprehensive classification
sanitize_path_key(key)                   – security: reject injection keys
append_team_memory_summary_parts(...)    – build summary text for collapse UI
count_team_memory_operations(tool_uses)  – batch-iterate tool uses for counts
get_team_memory_operation_counts(group)  – extract counts from collapse group
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

from hare.memdir.team_mem_paths import (  # noqa: F401  – re-exported
    get_team_mem_path,
    is_team_mem_file,
    is_team_memory_enabled,
)

_log = logging.getLogger(__name__)

FILE_EDIT_TOOL_NAME = "Edit"
FILE_WRITE_TOOL_NAME = "Write"
FILE_READ_TOOL_NAME = "Read"
NOTEBOOK_EDIT_TOOL_NAME = "NotebookEdit"

# Tool names that perform file reads.
_READ_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "Read",
        "ReadFile",
        "FileRead",
        "NotebookRead",
    }
)

# Tool names that perform directory listings.
_LIST_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "List",
        "Bash",
        "BashTool",
        "PowerShell",
        "Glob",
    }
)

# Tool names that perform search/grep operations.
_SEARCH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "Grep",
        "GrepTool",
        "Glob",
        "GlobTool",
        "Bash",
        "BashTool",
    }
)

# --- helpers ----------------------------------------------------------------

_PATH_TRAVERSAL_PATTERN = re.compile(
    r"(%[0-9a-fA-F]{2}|\.\.[/\\]|[\x00]|"
    r"[．／＼]|"  # fullwidth ../\
    r"[/\\]\.\.[/\\])"          # embedded ../
)


def _try_parse_json_input(tool_input: Any) -> Any:
    """If *tool_input* is a JSON string, parse and return it; else return as-is."""
    if isinstance(tool_input, str):
        stripped = tool_input.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass
        return tool_input
    return tool_input


def _get_string_field(tool_input: Any, *keys: str) -> str | None:
    """Extract the first present string-valued field from *keys*."""
    if not isinstance(tool_input, dict):
        return None
    for k in keys:
        v = tool_input.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _normalize_path(p: str) -> str:
    """Normalize to forward-slash form for reliable comparison."""
    return p.replace("\\", "/")


# --- public detection functions ----------------------------------------------


def is_team_memory_search(tool_input: Any) -> bool:
    """Check if a tool-use input targets team memory files via path, pattern,
    glob, or directory parameters.

    Handles:
    - ``path``:   file path        (Grep, Glob, Bash, Read)
    - ``pattern``: grep/glob pattern (Grep, Glob)
    - ``glob``:    glob expression  (Glob)
    - ``directory`` / ``dir``: directory to search in (Grep, Glob)

    Also handles JSON-string inputs (e.g. from saved tool_use blocks).
    """
    inp = _try_parse_json_input(tool_input)
    if not isinstance(inp, dict):
        return False

    # Direct file/directory path check (exact file match)
    file_path = _get_string_field(inp, "file_path", "path", "filePath")
    if file_path is not None and is_team_mem_file(file_path):
        return True

    # Directory path check (grep -R in team dir, glob in team dir)
    dir_path = _get_string_field(inp, "directory", "dir", "path")
    if dir_path is not None and is_team_memory_directory(dir_path):
        return True

    # Pattern / glob check (grep with team-mem file patterns, glob patterns)
    pattern = _get_string_field(inp, "pattern", "glob")
    if pattern is not None and is_team_memory_pattern(pattern):
        return True

    return False


def is_team_memory_write_or_edit(tool_name: str, tool_input: Any) -> bool:
    """Check if a Write, Edit, or NotebookEdit tool use targets a team
    memory file.

    Handles JSON-string inputs and multiple path-key conventions
    (``file_path``, ``filePath``, ``path``, ``notebook_path``).
    """
    if tool_name not in (
        FILE_WRITE_TOOL_NAME,
        FILE_EDIT_TOOL_NAME,
        NOTEBOOK_EDIT_TOOL_NAME,
    ):
        return False

    inp = _try_parse_json_input(tool_input)
    if not isinstance(inp, dict):
        return False

    fp = _get_string_field(inp, "file_path", "filePath", "path", "notebook_path")
    if fp is None:
        return False

    return is_team_mem_file(fp)


def is_team_memory_read(tool_input: Any) -> bool:
    """Check if a Read/FileRead/NBRead tool use targets a team memory file.

    Handles JSON-string inputs and multiple path-key conventions.
    """
    inp = _try_parse_json_input(tool_input)
    if not isinstance(inp, dict):
        return False

    fp = _get_string_field(inp, "file_path", "filePath", "path", "notebook_path")
    if fp is None:
        return False

    return is_team_mem_file(fp)


def is_team_memory_pattern(pattern: str) -> bool:
    """Check if a glob or grep pattern targets the team memory directory.

    Examples that match:
      - ``.../memory/team/**``
      - ``.../team/*.md``
      - ``team/MEMORY.md``
      - ``.../memory/team/`` (directory prefix)

    Does NOT require team memory to be enabled — this is a pure string
    check so that the feature-flag check happens at the caller level.
    """
    if not pattern or not isinstance(pattern, str):
        return False

    norm = _normalize_path(pattern)

    # Direct team directory references
    if "team/" in norm or "/team" in norm or "team\\" in norm:
        return True

    # Check against the resolved team memory path if available
    try:
        team_path = _normalize_path(get_team_mem_path().rstrip("/\\"))
        if team_path in norm:
            return True
    except Exception:
        pass

    return False


def is_team_memory_directory(dir_path: str) -> bool:
    """Check if a directory path is within the team memory directory.

    Checks whether the directory is exactly the team memory directory or
    is a subdirectory within it.
    """
    if not dir_path or not isinstance(dir_path, str):
        return False

    norm = _normalize_path(dir_path)

    # Shortcut: catch direct naming
    if "team" in norm:
        # More precise: check against the known team mem path
        try:
            team_path = _normalize_path(get_team_mem_path().rstrip("/\\"))
            if norm == team_path or norm.startswith(team_path + "/"):
                return True
        except Exception:
            pass

        # Fallback: check if it ends with /team or contains /team/
        if norm.endswith("/team") or "/team/" in norm or norm.endswith("/memory/team"):
            return True

    return False


# --- security ----------------------------------------------------------------


class PathKeyValidationError(ValueError):
    """Raised when a team-memory path key fails security validation."""


def sanitize_path_key(key: str) -> str:
    """Validate and sanitize a relative path key for team memory access.

    Ported from the TypeScript ``sanitizePathKey()`` in ``teamMemPaths.ts``.
    Rejects injection vectors that could escape the team memory directory:

    * Null bytes (C-string truncation)
    * URL-encoded traversal  (``%2e%2e%2f`` → ``../``)
    * Unicode-normalized traversal  (fullwidth ``．．／`` → ``../``)
    * Backslash separators (Windows path traversal)
    * Absolute paths (``/etc/passwd``)

    Returns the sanitized key unchanged on success.
    Raises ``PathKeyValidationError`` on rejection.

    .. note::

       This is a **synchronous** validation suitable for non-async callers.
       The deeper symlink-resolution checks live in
       :func:`hare.memdir.team_mem_paths.validate_team_mem_key`.
    """
    if not isinstance(key, str):
        raise PathKeyValidationError(f"Path key must be a string, got {type(key).__name__}")

    # Null bytes can truncate paths in C-based syscalls
    if "\0" in key:
        raise PathKeyValidationError(f"Null byte in path key: {key!r}")

    # Plain dot-dot traversal (e.g. ../etc/passwd)
    if ".." in key:
        raise PathKeyValidationError(
            f"Path traversal (..) in path key: {key!r}"
        )

    # URL-encoded traversals (e.g. %2e%2e%2f = ../)
    decoded = key
    try:
        from urllib.parse import unquote

        decoded = unquote(key)
    except Exception:
        # Malformed percent-encoding — not a valid URL-encoded traversal
        pass

    if decoded != key:
        if ".." in decoded or "/" in decoded or "\\" in decoded:
            raise PathKeyValidationError(
                f"URL-encoded traversal in path key: {key!r}"
            )

    # Unicode normalization attacks: fullwidth characters normalize to ASCII
    # ../ under NFKC. While path.resolve/fs.writeFile treat these as literal
    # bytes, downstream layers may normalize — reject for defense-in-depth.
    normalized = unicodedata.normalize("NFKC", key)
    if normalized != key:
        if (
            ".." in normalized
            or "/" in normalized
            or "\\" in normalized
            or "\0" in normalized
        ):
            raise PathKeyValidationError(
                f"Unicode-normalized traversal in path key: {key!r}"
            )

    # Reject backslashes (Windows path separator used as traversal vector)
    if "\\" in key:
        raise PathKeyValidationError(f"Backslash in path key: {key!r}")

    # Reject absolute paths
    if key.startswith("/") or (len(key) >= 3 and key[1:3] == ":\\"):
        raise PathKeyValidationError(f"Absolute path key: {key!r}")

    return key


# --- comprehensive classification -------------------------------------------


def classify_tool_use_for_team_memory(
    tool_name: str,
    tool_input: Any,
) -> dict[str, bool]:
    """Classify a single tool use as targeting team memory in various ways.

    Returns a dict with boolean flags:
      - ``is_team_mem_write``: Write/Edit targeting team memory
      - ``is_team_mem_read``: Read targeting team memory
      - ``is_team_mem_search``: Search (Grep/Glob) targeting team memory
      - ``is_team_mem_list``: List/directory targeting team memory
      - ``is_team_mem_any``: any of the above

    This is a convenience wrapper used by batch counters and collapse logic
    to avoid calling multiple individual functions.
    """
    inp = _try_parse_json_input(tool_input)

    result: dict[str, bool] = {
        "is_team_mem_write": False,
        "is_team_mem_read": False,
        "is_team_mem_search": False,
        "is_team_mem_list": False,
        "is_team_mem_any": False,
    }

    if not is_team_memory_enabled():
        return result

    # --- write check ---
    result["is_team_mem_write"] = is_team_memory_write_or_edit(tool_name, tool_input)

    # --- read check ---
    if tool_name in _READ_TOOL_NAMES:
        result["is_team_mem_read"] = is_team_memory_read(inp)

    # --- search check ---
    if tool_name in _SEARCH_TOOL_NAMES:
        result["is_team_mem_search"] = is_team_memory_search(inp)

    # --- list check ---
    if tool_name in _LIST_TOOL_NAMES:
        dir_path = (
            _get_string_field(inp, "directory", "dir", "path")
            if isinstance(inp, dict)
            else None
        )
        result["is_team_mem_list"] = (
            dir_path is not None and is_team_memory_directory(dir_path)
        )

    result["is_team_mem_any"] = any(
        result[k]
        for k in ("is_team_mem_write", "is_team_mem_read", "is_team_mem_search", "is_team_mem_list")
    )

    return result


# --- batch counting ----------------------------------------------------------

_MemoryOperationCounts = dict[str, int]


def count_team_memory_operations(
    tool_uses: list[dict[str, Any]],
) -> _MemoryOperationCounts:
    """Iterate a batch of tool-use records and return team-memory operation counts.

    Each element in *tool_uses* is expected to be a dict with keys:
      - ``name`` (str): tool name
      - ``input`` (Any): tool input dict or JSON string

    Returns a dict with keys:
      - ``team_memory_write_count``
      - ``team_memory_read_count``
      - ``team_memory_search_count``
      - ``team_memory_list_count``
      - ``team_memory_total_count``
    """
    counts: _MemoryOperationCounts = {
        "team_memory_write_count": 0,
        "team_memory_read_count": 0,
        "team_memory_search_count": 0,
        "team_memory_list_count": 0,
        "team_memory_total_count": 0,
    }

    if not is_team_memory_enabled():
        return counts

    for use in tool_uses:
        if not isinstance(use, dict):
            continue
        name: str = use.get("name", "") or use.get("tool_name", "") or ""
        inp = use.get("input")
        result = classify_tool_use_for_team_memory(name, inp)
        if not result.get("is_team_mem_any"):
            continue
        if result.get("is_team_mem_write"):
            counts["team_memory_write_count"] += 1
        if result.get("is_team_mem_read"):
            counts["team_memory_read_count"] += 1
        if result.get("is_team_mem_search"):
            counts["team_memory_search_count"] += 1
        if result.get("is_team_mem_list"):
            counts["team_memory_list_count"] += 1

    counts["team_memory_total_count"] = (
        counts["team_memory_write_count"]
        + counts["team_memory_read_count"]
        + counts["team_memory_search_count"]
        + counts["team_memory_list_count"]
    )

    return counts


def get_team_memory_operation_counts(
    group: dict[str, Any] | None,
) -> dict[str, int]:
    """Extract team-memory counters from a collapse group dict.

    The collapse engine stores team-memory counts inside the group using
    keys that match the names expected by ``append_team_memory_summary_parts``.

    Returns a safe dict with all keys present (defaulting to 0 when the
    group is ``None`` or missing a key).
    """
    if not isinstance(group, dict):
        group = {}

    def _int(key: str, default: int = 0) -> int:
        v = group.get(key, default)
        if isinstance(v, int):
            return v
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    # Read count: if the group stores a set of file paths, use its size.
    read_file_paths = group.get("teamMemoryReadFilePaths")
    if isinstance(read_file_paths, (set, frozenset, list, tuple)):
        read_count = len(read_file_paths)
    else:
        read_count = _int("teamMemoryReadCount")

    return {
        "team_memory_read_count": read_count,
        "team_memory_search_count": _int("teamMemorySearchCount"),
        "team_memory_write_count": _int("teamMemoryWriteCount"),
    }


# --- summary builder ---------------------------------------------------------


def append_team_memory_summary_parts(
    memory_counts: dict[str, int] | None,
    is_active: bool,
    parts: list[str],
) -> None:
    """Append human-readable team-memory summary fragments to *parts*.

    Encapsulates all team-memory verb/string logic for
    ``get_search_read_summary_text`` and other collapse summarisers.

    *memory_counts* is a dict that may contain any of:
      - ``team_memory_read_count``
      - ``team_memory_search_count``
      - ``team_memory_write_count``

    Missing keys and ``None`` dicts are treated as zero.

    The *parts* list is mutated in-place.  Callers are responsible for
    joining and punctuation.
    """
    if memory_counts is None:
        memory_counts = {}

    tr = memory_counts.get("team_memory_read_count", 0) or 0
    ts = memory_counts.get("team_memory_search_count", 0) or 0
    tw = memory_counts.get("team_memory_write_count", 0) or 0

    # Also accept camelCase variants (from collapse engine group dicts).
    if tr == 0 and ts == 0 and tw == 0:
        tr = memory_counts.get("teamMemoryReadCount", 0) or 0
        ts = memory_counts.get("teamMemorySearchCount", 0) or 0
        tw = memory_counts.get("teamMemoryWriteCount", 0) or 0

    if tr > 0:
        if is_active:
            v = "Recalling" if not parts else "recalling"
        else:
            v = "Recalled" if not parts else "recalled"
        parts.append(f"{v} {tr} team {'memory' if tr == 1 else 'memories'}")

    if ts > 0:
        if is_active:
            v = "Searching" if not parts else "searching"
        else:
            v = "Searched" if not parts else "searched"
        parts.append(f"{v} team memories")

    if tw > 0:
        if is_active:
            v = "Writing" if not parts else "writing"
        else:
            v = "Wrote" if not parts else "wrote"
        parts.append(f"{v} {tw} team {'memory' if tw == 1 else 'memories'}")
