"""Session file access analytics hooks (port of sessionFileAccessHooks.ts)."""

from __future__ import annotations

from typing import Any


def is_memory_file_access(tool_name: str, tool_input: Any) -> bool:
    """Return True when tool targets session memory or memdir paths — extend with memoryFileDetection."""
    _ = (tool_name, tool_input)
    return False


def register_session_file_access_hooks() -> None:
    """Register PostToolUse hooks — wire bootstrap.register_hook_callbacks when available."""
    try:
        from hare.bootstrap import hooks as hook_registry

        _ = hook_registry
    except ImportError:
        pass
