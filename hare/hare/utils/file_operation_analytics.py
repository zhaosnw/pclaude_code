"""
Privacy-preserving hashes for file operation analytics.

Port of: src/utils/fileOperationAnalytics.ts
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from hare.utils.debug import log_for_debugging


def _log_event(_e, _m):
    return None


def set_analytics_log_event(fn: Any) -> None:
    global _log_event
    _log_event = fn


def _hash_file_path(file_path: str) -> str:
    return hashlib.sha256(file_path.encode()).hexdigest()[:16]


def _hash_file_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


MAX_CONTENT_HASH_SIZE = 100 * 1024


def log_file_operation(
    *,
    operation: Literal["read", "write", "edit"],
    tool: Literal["FileReadTool", "FileWriteTool", "FileEditTool"],
    file_path: str,
    content: str | None = None,
    type: Literal["create", "update"] | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "operation": operation,
        "tool": tool,
        "filePathHash": _hash_file_path(file_path),
    }
    if content is not None and len(content) <= MAX_CONTENT_HASH_SIZE:
        metadata["contentHash"] = _hash_file_content(content)
    if type is not None:
        metadata["type"] = type
    try:
        _log_event("tengu_file_operation", metadata)
    except Exception as e:  # noqa: BLE001
        log_for_debugging(str(e))
