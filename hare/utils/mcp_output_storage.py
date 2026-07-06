"""Persist large MCP binary outputs — port of `mcpOutputStorage.ts`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from hare.utils.format import format_bytes
from hare.utils.log import log_error

MCPResultType = Literal["toolResult", "structuredContent", "contentArray"]


def get_format_description(kind: MCPResultType, schema: object | None = None) -> str:
    if kind == "toolResult":
        return "Plain text"
    if kind == "structuredContent":
        return f"JSON with schema: {schema}" if schema else "JSON"
    return f"JSON array with schema: {schema}" if schema else "JSON array"


def get_large_output_instructions(
    raw_output_path: str,
    content_length: int,
    format_description: str,
    max_read_length: int | None = None,
) -> str:
    base = (
        f"Error: result ({content_length:,} characters) exceeds maximum allowed tokens. "
        f"Output has been saved to {raw_output_path}.\n"
        f"Format: {format_description}\n"
        "Use offset and limit parameters to read specific portions of the file, search within it for specific content, "
        "and jq to make structured queries.\n"
        "REQUIREMENTS FOR SUMMARIZATION/ANALYSIS/REVIEW:\n"
        f"- You MUST read the content from the file at {raw_output_path} in sequential chunks until 100% of the content has been read.\n"
    )
    trunc = (
        f'- If you receive truncation warnings when reading the file ("[N lines truncated]"), reduce the chunk size until you have read '
        f"100% of the content without truncation ***DO NOT PROCEED UNTIL YOU HAVE DONE THIS***. Bash output is limited to {max_read_length:,} chars.\n"
        if max_read_length
        else "- If you receive truncation warnings when reading the file, reduce the chunk size until you have read 100% of the content without truncation.\n"
    )
    tail = "- Before producing ANY summary or analysis, you MUST explicitly describe what portion of the content you have read. ***If you did not read the entire content, you MUST explicitly state this.***\n"
    return base + trunc + tail


def extension_for_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return "bin"
    mt = (mime_type.split(";")[0] or "").strip().lower()
    mapping = {
        "application/pdf": "pdf",
        "application/json": "json",
        "text/csv": "csv",
        "text/plain": "txt",
        "text/html": "html",
        "text/markdown": "md",
        "application/zip": "zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/msword": "doc",
        "application/vnd.ms-excel": "xls",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/svg+xml": "svg",
    }
    return mapping.get(mt, "bin")


def is_binary_content_type(content_type: str) -> bool:
    if not content_type:
        return False
    mt = (content_type.split(";")[0] or "").strip().lower()
    if mt.startswith("text/"):
        return False
    if mt.endswith("+json") or mt == "application/json":
        return False
    if mt.endswith("+xml") or mt == "application/xml":
        return False
    if mt.startswith("application/javascript"):
        return False
    if mt == "application/x-www-form-urlencoded":
        return False
    return True


def persist_binary_content(
    bytes_data: bytes,
    mime_type: str | None,
    persist_id: str,
    *,
    ensure_dir: Any | None = None,
    tool_results_dir: Any | None = None,
) -> dict[str, Any]:
    """Wire `ensure_tool_results_dir` / `get_tool_results_dir` at app layer."""
    try:
        from hare.utils.tool_result_storage import (
            ensure_tool_results_dir,
            get_tool_results_dir,
        )  # type: ignore[import-not-found]
    except ImportError:

        def ensure_tool_results_dir() -> None:  # noqa: F811
            pass

        def get_tool_results_dir() -> str:
            return os.path.join(os.path.expanduser("~"), ".hare", "tool-results")

    try:
        ensure_tool_results_dir()
        ext = extension_for_mime_type(mime_type)
        filepath = str(Path(get_tool_results_dir()) / f"{persist_id}.{ext}")
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        Path(filepath).write_bytes(bytes_data)
        return {"filepath": filepath, "size": len(bytes_data), "ext": ext}
    except Exception as e:
        log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return {"error": str(e)}


def get_binary_blob_saved_message(
    filepath: str, mime_type: str | None, size: int, source_description: str
) -> str:
    mt = mime_type or "unknown type"
    return f"{source_description}Binary content ({mt}, {format_bytes(size)}) saved to {filepath}"
