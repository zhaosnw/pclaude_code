"""
Log and transcript types — the full transcript-entry type system.

Port of: src/types/logs.ts (331 lines)

Defines the complete transcript format: SerializedMessage, LogOption,
TranscriptMessage, and all 15+ entry variants for session storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

LogLevel = Literal["debug", "info", "warn", "error"]


@dataclass
class LogEntry:
    level: LogLevel
    message: str
    timestamp: float = 0.0
    source: str = ""
    metadata: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Serialized messages — the core transcript format
# ---------------------------------------------------------------------------


@dataclass
class SerializedMessage:
    """A serialized message in the transcript JSONL."""

    type: str = (
        ""  # 'user' | 'assistant' | 'system' | 'progress' | 'tool_use' | 'tool_result'
    )
    session_id: str = ""
    uuid: str = ""
    parent_uuid: Optional[str] = None
    message: dict[str, Any] = field(default_factory=dict)
    is_sidechain: bool = False
    is_virtual: bool = False
    is_meta: bool = False
    is_compact_summary: bool = False
    origin: Optional[dict[str, Any]] = None
    timestamp: str = ""


# ---------------------------------------------------------------------------
# LogOption — session metadata for /resume
# ---------------------------------------------------------------------------


@dataclass
class LogOption:
    date: str = ""
    messages: list[SerializedMessage] = field(default_factory=list)
    full_path: str = ""
    value: float = 0.0  # modified timestamp for sorting
    created: str = ""  # ISO date
    modified: str = ""
    first_prompt: str = ""
    message_count: int = 0
    is_sidechain: bool = False
    session_id: str = ""
    custom_title: Optional[str] = None
    content_replacements: list[dict[str, Any]] = field(default_factory=list)
    tag: Optional[str] = None
    mode: Optional[str] = None
    agent_name: Optional[str] = None
    agent_color: Optional[str] = None
    worktree_session: Optional[dict[str, Any]] = None
    is_lite: bool = False


# ---------------------------------------------------------------------------
# Transcript entry variants
# ---------------------------------------------------------------------------


@dataclass
class TranscriptMessage:
    """A main-conversation message entry in the transcript."""

    type: str = ""  # 'user' | 'assistant' | 'system' | 'progress'
    session_id: str = ""
    uuid: str = ""
    parent_uuid: Optional[str] = None
    message: dict[str, Any] = field(default_factory=dict)
    is_sidechain: bool = False
    timestamp: str = ""


@dataclass
class SummaryMessage:
    """Summary entry: compacted conversation prefix."""

    type: Literal["summary"] = "summary"
    session_id: str = ""
    summary: str = ""
    num_messages: int = 0


@dataclass
class TagMessage:
    """Tag entry: session tag assignment."""

    type: Literal["tag"] = "tag"
    session_id: str = ""
    tag: str = ""


@dataclass
class TitleMessage:
    """Title entry: session title update."""

    type: Literal["title"] = "title"
    session_id: str = ""
    title: str = ""


@dataclass
class ModeMessage:
    """Mode entry: session mode (normal/coordinator/plan)."""

    type: Literal["mode"] = "mode"
    session_id: str = ""
    mode: str = ""


@dataclass
class AgentNameMessage:
    """Agent name entry: standalone agent display name."""

    type: Literal["agent_name"] = "agent_name"
    session_id: str = ""
    name: str = ""


@dataclass
class AgentColorMessage:
    """Agent color entry: standalone agent display color."""

    type: Literal["agent_color"] = "agent_color"
    session_id: str = ""
    color: str = ""


@dataclass
class PRLinkMessage:
    """PR link entry: pull request URL."""

    type: Literal["pr_link"] = "pr_link"
    session_id: str = ""
    url: str = ""


@dataclass
class AttributionSnapshotMessage:
    """Attribution snapshot for commit messages."""

    type: Literal["attribution_snapshot"] = "attribution_snapshot"
    session_id: str = ""
    attribution: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorktreeStateEntry:
    """Worktree state for session resume."""

    type: Literal["worktree_state"] = "worktree_state"
    session_id: str = ""
    worktree_name: str = ""
    original_cwd: str = ""


@dataclass
class ContentReplacementEntry:
    """Content replacement records for per-message budget."""

    type: Literal["content_replacement"] = "content_replacement"
    session_id: str = ""
    replacements: list[dict[str, Any]] = field(default_factory=list)


# Discriminated union of all entry types
Entry = Union[
    TranscriptMessage,
    SummaryMessage,
    TagMessage,
    TitleMessage,
    ModeMessage,
    AgentNameMessage,
    AgentColorMessage,
    PRLinkMessage,
    AttributionSnapshotMessage,
    WorktreeStateEntry,
    ContentReplacementEntry,
    SerializedMessage,
]


def is_transcript_message(entry: dict[str, Any]) -> bool:
    """Type guard for main conversation messages."""
    return entry.get("type") in ("user", "assistant", "system", "progress")
