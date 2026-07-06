"""
FileEditTool — edit files with string replacement.

Port of: src/tools/FileEditTool/FileEditTool.ts
"""

from __future__ import annotations

import os
import difflib
import subprocess
from typing import Any

from .file_edit_utils import (
    find_actual_string,
    normalize_file_edit_input,
    preserve_quote_style,
)

TOOL_NAME = "Edit"
FILE_EDIT_TOOL_NAME = TOOL_NAME
ALIASES: list[str] = []
SEARCH_HINT = "modify file contents in place"

MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "old_string": {"type": "string", "description": "The text to replace"},
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must be different from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["create", "update"],
                "description": "Whether a new file was created or existing updated",
            },
            "filePath": {
                "type": "string",
                "description": "The path to the file that was edited",
            },
            "structuredPatch": {
                "type": "array",
                "description": "Unified diff hunks showing the changes",
            },
            "originalFile": {
                "type": "string",
                "description": "Original file content before edit",
            },
        },
        "required": ["type", "filePath", "structuredPatch"],
    }


def is_read_only(input: dict[str, Any]) -> bool:
    return False


def is_destructive(input: dict[str, Any]) -> bool:
    return True


def validate_input(input: dict[str, Any]) -> dict[str, Any]:
    """Validate edit input before execution.

    Returns {'result': True} if valid, or {'result': False, 'message': ..., 'errorCode': N}.
    """
    file_path = input.get("file_path", "")
    old_string = input.get("old_string", "")
    new_string = input.get("new_string", "")
    replace_all = input.get("replace_all", False)

    # Expand path
    full_path = os.path.expanduser(file_path)
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(full_path)

    if old_string == new_string:
        return {
            "result": False,
            "message": "No changes to make: old_string and new_string are exactly the same.",
            "errorCode": 1,
        }

    # Skip UNC paths
    if full_path.startswith("\\\\") or full_path.startswith("//"):
        return {"result": True}

    # Check file exists and size
    try:
        stat = os.stat(full_path)
        if os.path.isfile(full_path) and stat.st_size > MAX_EDIT_FILE_SIZE:
            size_mb = stat.st_size / (1024 * 1024)
            return {
                "result": False,
                "message": f"File is too large to edit ({size_mb:.0f}MB). Maximum editable file size is 1GiB.",
                "errorCode": 10,
            }
    except FileNotFoundError:
        # File doesn't exist yet
        if old_string == "":
            return {"result": True}
        # Try find similar file
        similar = _find_similar_file(full_path)
        cwd = os.getcwd()
        msg = f"File does not exist. Current working directory: {cwd}."
        if similar:
            msg += f" Did you mean {similar}?"
        return {"result": False, "message": msg, "errorCode": 4}
    except OSError as e:
        return {"result": False, "message": str(e), "errorCode": 10}

    # Read file content
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            file_content = f.read().replace("\r\n", "\n")
    except Exception as e:
        if old_string == "":
            return {"result": True}  # Create new file
        return {"result": False, "message": str(e), "errorCode": 4}

    # Empty old_string with non-empty file
    if old_string == "":
        if file_content.strip():
            return {
                "result": False,
                "message": "Cannot create new file - file already exists.",
                "errorCode": 3,
            }
        return {"result": True}

    # Notebook check
    if full_path.endswith(".ipynb"):
        return {
            "result": False,
            "message": "File is a Jupyter Notebook. Use the NotebookEdit tool to edit this file.",
            "errorCode": 5,
        }

    # Check file was read recently
    read_state = _read_file_state.get(full_path)
    if read_state is None:
        return {
            "result": False,
            "message": "File has not been read yet. Read it first before writing to it.",
            "errorCode": 6,
        }

    # Check file modification time vs read time
    try:
        last_write = os.path.getmtime(full_path)
        if last_write > read_state.get("timestamp", 0):
            # Compare content to avoid false positive
            is_full = not read_state.get("offset") and not read_state.get("limit")
            if is_full and file_content == read_state.get("content"):
                pass  # Content unchanged, safe to proceed
            else:
                return {
                    "result": False,
                    "message": "File has been modified since read, either by the user or by a linter. "
                    "Read it again before attempting to write it.",
                    "errorCode": 7,
                }
    except OSError:
        pass

    # Find the actual string in file (handle curly<->straight quote differences)
    actual_old = find_actual_string(file_content, old_string)
    if actual_old is None:
        return {
            "result": False,
            "message": "The string to replace was not found in the file.",
            "errorCode": 8,
        }
    count = len(file_content.split(actual_old)) - 1
    if count > 1 and not replace_all:
        return {
            "result": False,
            "message": f"The string was found {count} times in the file. "
            f"Add more context to make it unique, or use replace_all=true.",
            "errorCode": 9,
        }

    return {"result": True}


_read_file_state: dict[str, dict[str, Any]] = {}


def mark_file_read(
    file_path: str,
    offset: int | None = None,
    limit: int | None = None,
    content: str = "",
) -> None:
    """Record that a file was read (called by FileReadTool)."""
    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        mtime = 0
    _read_file_state[os.path.abspath(file_path)] = {
        "timestamp": mtime,
        "offset": offset,
        "limit": limit,
        "content": content,
    }


def _find_similar_file(path: str) -> str | None:
    """Find a file with similar name in the same directory."""
    dir_path = os.path.dirname(path)
    base = os.path.basename(path)
    if not os.path.isdir(dir_path):
        return None
    try:
        files = os.listdir(dir_path)
        # Simple: check same name different extension
        stem, _ = os.path.splitext(base)
        for f in files:
            if f.startswith(stem) and f != base:
                return os.path.join(dir_path, f)
    except OSError:
        pass
    return None


async def call(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute a file edit.

    Performs safe string replacement with diff output and file history tracking.
    """
    # Request-side normalization (port of normalizeToolInput -> normalizeFileEditInput):
    # strip trailing whitespace on new_string (except markdown) and de-sanitize
    # tokens Claude can't see, so matching/writing mirrors the reference.
    old_string, new_string = normalize_file_edit_input(file_path, old_string, new_string)

    # Validate first
    validation = validate_input(
        {
            "file_path": file_path,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        }
    )
    if not validation.get("result"):
        return {"error": validation.get("message", "Validation failed")}

    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)
    file_path = os.path.abspath(file_path)

    # Read original content
    existed = os.path.isfile(file_path)
    original = ""
    if existed:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read().replace("\r\n", "\n")
        except Exception as e:
            return {"error": f"Cannot read file: {e}"}

    # Empty old_string on non-existent or empty file = create
    if old_string == "" and (not existed or not original.strip()):
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8", newline="") as f:
                f.write(new_string)
            line_count = new_string.count("\n") + (
                1 if new_string and not new_string.endswith("\n") else 0
            )
            return {
                "data": f"Created {file_path} ({line_count} lines)",
                "file_path": file_path,
                "type": "create",
                "structuredPatch": [],
                "originalFile": "",
            }
        except Exception as e:
            return {"error": str(e)}

    # Resolve curly<->straight quote differences, then preserve the file's curly
    # style in the replacement (findActualString/preserveQuoteStyle in the ref).
    actual_old = find_actual_string(original, old_string) or old_string
    actual_new = preserve_quote_style(old_string, actual_old, new_string)
    old_string, new_string = actual_old, actual_new

    # Replace. Faithful port of applyEditToFile (src/tools/FileEditTool/utils.ts):
    # when deleting (new_string == "") and old_string does not end with a newline
    # but appears followed by one in the file, remove that trailing newline too so
    # deleting a whole line doesn't leave a blank line behind.
    if new_string == "" and not old_string.endswith("\n") and (old_string + "\n") in original:
        search = old_string + "\n"
    else:
        search = old_string
    count = original.count(search)
    if replace_all:
        new_content = original.replace(search, new_string)
        replacements = count
    else:
        new_content = original.replace(search, new_string, 1)
        replacements = 1

    # Generate diff
    patch = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            n=3,
        )
    )

    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)

        # Update read state
        mark_file_read(file_path, content=new_content)

        # Try git diff for structured patch
        git_diff = None
        try:
            result = subprocess.run(
                ["git", "diff", "--no-color", "--", file_path],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=os.getcwd(),
            )
            if result.returncode == 0 and result.stdout.strip():
                git_diff = result.stdout.strip()
        except Exception:
            pass

        return {
            "data": f"Edited {file_path} ({replacements} replacement{'s' if replacements > 1 else ''})",
            "file_path": file_path,
            "type": "update",
            "structuredPatch": patch,
            "originalFile": original,
            "gitDiff": git_diff,
        }
    except Exception as e:
        return {"error": str(e)}


def inputs_equivalent(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Check if two edit inputs are functionally equivalent."""
    return (
        a.get("file_path") == b.get("file_path")
        and a.get("old_string") == b.get("old_string")
        and a.get("new_string") == b.get("new_string")
        and a.get("replace_all") == b.get("replace_all")
    )
