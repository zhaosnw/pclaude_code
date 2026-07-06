"""
FileWriteTool — write files to the filesystem.

Port of: src/tools/FileWriteTool/FileWriteTool.ts
"""

from __future__ import annotations

import difflib
import os
import subprocess
from typing import Any

from hare.tools_impl.FileEditTool.file_edit_utils import (
    is_markdown_path,
    strip_trailing_whitespace,
)

TOOL_NAME = "Write"
FILE_WRITE_TOOL_NAME = TOOL_NAME
ALIASES: list[str] = []
SEARCH_HINT = "create or overwrite files"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute, not relative)",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
    }


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["create", "update"],
                "description": "Whether a new file was created or an existing file was updated",
            },
            "filePath": {
                "type": "string",
                "description": "The path to the file that was written",
            },
            "content": {
                "type": "string",
                "description": "The content that was written to the file",
            },
            "structuredPatch": {
                "type": "array",
                "description": "Diff patch showing the changes",
            },
            "originalFile": {
                "type": "string",
                "description": "The original file content before the write (null for new files)",
            },
        },
        "required": ["type", "filePath", "content", "structuredPatch"],
    }


def is_read_only(input: dict[str, Any]) -> bool:
    return False


def is_destructive(input: dict[str, Any]) -> bool:
    return True


def validate_input(input: dict[str, Any]) -> dict[str, Any]:
    """Validate write input before execution."""
    file_path = input.get("file_path", "")
    content = input.get("content", "")

    if not file_path:
        return {
            "result": False,
            "message": "file_path is required.",
            "errorCode": 1,
        }

    # Expand and make absolute
    full_path = os.path.expanduser(file_path)
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(full_path)

    # Must be a file path (not a directory)
    if full_path.endswith(os.sep):
        return {
            "result": False,
            "message": "file_path must be a file, not a directory.",
            "errorCode": 2,
        }

    return {"result": True}


async def call(
    file_path: str,
    content: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute a file write.

    Creates or overwrites a file, producing a structured diff of changes.
    """
    # Request-side normalization (port of normalizeToolInput): strip trailing
    # whitespace per line, except for markdown where two trailing spaces are a
    # hard line break.
    if not is_markdown_path(file_path):
        content = strip_trailing_whitespace(content)

    validation = validate_input({"file_path": file_path, "content": content})
    if not validation.get("result"):
        return {"error": validation.get("message", "Validation failed")}

    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)
    file_path = os.path.abspath(file_path)

    existed = os.path.isfile(file_path)
    original = ""

    if existed:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read().replace("\r\n", "\n")
        except Exception:
            original = ""

    try:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Write the file
        with open(file_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)

        line_count = content.count("\n") + (
            1 if content and not content.endswith("\n") else 0
        )

        # Generate unified diff
        patch = list(
            difflib.unified_diff(
                original.splitlines(keepends=True) if existed else [],
                content.splitlines(keepends=True),
                fromfile=file_path,
                tofile=file_path,
                n=3,
            )
        )

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

        # Update FileEditTool's read state so subsequent edits can proceed
        try:
            from hare.tools_impl.FileEditTool.file_edit_tool import mark_file_read

            mark_file_read(file_path, content=content)
        except ImportError:
            pass

        return {
            "data": f"{'Updated' if existed else 'Created'} {file_path} ({line_count} lines)",
            "file_path": file_path,
            "type": "update" if existed else "create",
            "content": content,
            "structuredPatch": patch,
            "originalFile": original if existed else None,
            "gitDiff": git_diff,
            "created": not existed,
        }
    except IOError as e:
        return {"error": str(e)}


def inputs_equivalent(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Check if two write inputs are functionally equivalent."""
    return a.get("file_path") == b.get("file_path") and a.get("content") == b.get(
        "content"
    )
