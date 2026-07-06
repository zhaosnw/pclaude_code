"""
Text input types for prompt/command queue and input state management.

Port of: src/types/textInputTypes.ts (388 lines)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

# ---------------------------------------------------------------------------
# Input modes
# ---------------------------------------------------------------------------

PromptInputMode = Literal["bash", "prompt", "orphaned-permission", "task-notification"]
EditablePromptInputMode = Literal["bash", "prompt"]
QueuePriority = Literal["now", "next", "later"]
VimMode = Literal["INSERT", "NORMAL", "VISUAL", "VISUAL_LINE"]


# ---------------------------------------------------------------------------
# Queued command
# ---------------------------------------------------------------------------


@dataclass
class QueuedCommand:
    """A queued command waiting to be processed."""

    value: Union[str, list[dict[str, Any]]] = ""
    mode: PromptInputMode = "prompt"
    priority: Optional[QueuePriority] = None
    uuid: Optional[str] = None
    skip_slash_commands: bool = False
    bridge_origin: bool = False
    is_meta: bool = False
    origin: Optional[str] = None
    workload: Optional[str] = None
    agent_id: Optional[str] = None
    # Additional fields matching TS
    is_synthetic: bool = False
    is_replay: bool = False
    parent_tool_use_id: Optional[str] = None
    source: Optional[str] = None
    scheduled_task_id: Optional[str] = None
    queue_timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Input state
# ---------------------------------------------------------------------------


@dataclass
class BaseInputState:
    """Base input state shared across modes."""

    value: str = ""
    cursor_position: int = 0
    mode: EditablePromptInputMode = "prompt"
    is_empty: bool = True
    is_modified: bool = False


@dataclass
class VimInputState:
    """Vim-specific input state."""

    mode: VimMode = "NORMAL"
    register: str = ""
    last_change: str = ""
    undo_stack: list[str] = field(default_factory=list)
    redo_stack: list[str] = field(default_factory=list)
    visual_start: int = 0
    visual_end: int = 0
    command_buffer: str = ""


@dataclass
class InlineGhostText:
    """Ghost text shown inline for autocomplete."""

    text: str = ""
    position: int = 0
    source: str = ""


# ---------------------------------------------------------------------------
# Orphaned permission
# ---------------------------------------------------------------------------


@dataclass
class OrphanedPermission:
    """Permission result for an orphaned tool use (no active prompt)."""

    permission_result: Optional[dict[str, Any]] = None
    assistant_message: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Image paste helpers
# ---------------------------------------------------------------------------


def is_valid_image_paste(content: dict[str, Any]) -> bool:
    return content.get("type") == "image" and len(content.get("content", "")) > 0


def get_image_paste_ids(
    pasted_contents: Optional[dict[int, dict[str, Any]]],
) -> Optional[list[int]]:
    if not pasted_contents:
        return None
    ids = [c["id"] for c in pasted_contents.values() if is_valid_image_paste(c)]
    return ids if ids else None
