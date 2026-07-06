"""MCP instructions delta for conversation attachments — port of `mcpInstructionsDelta.ts`.

Computes a diff between currently connected MCP servers (that carry instructions)
and what has already been announced via persisted delta attachments earlier in the
conversation. Returns null when nothing changed.

Instructions are immutable for the lifetime of an MCP connection (set once at
handshake), so the scan diffs on server NAME, not on content.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from hare.utils.env_utils import is_env_defined_falsy, is_env_truthy

# Real imports for feature-flag and analytics. Keep try/except so the module
# remains importable even before the full analytics stack is wired up.
try:
    from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale as _gb_cached
except ImportError:
    _gb_cached = None  # type: ignore[assignment]

try:
    from hare.services.analytics.event_logger import log_event as _log_event_real
except ImportError:
    _log_event_real = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Fallback stubs — used only when the real analytics stack is not yet wired
# ---------------------------------------------------------------------------

def _get_feature_value_cached_may_be_stale(key: str, default: bool = False) -> bool:
    """GrowthBook feature flag lookup with fallback.

    Uses the real GrowthBook client when available; otherwise returns
    the caller-supplied default.
    """
    if _gb_cached is not None:
        return _gb_cached(key, default)
    return default


def _log_event(name: str, payload: dict[str, Any]) -> None:
    """Analytics event logger with fallback.

    Uses the real event-logging pipeline when available; otherwise
    silently drops the event (queue-less stub).
    """
    if _log_event_real is not None:
        _log_event_real(name, payload)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class McpInstructionsDelta:
    """A diff between current connected MCP servers and prior announcements.

    Attributes:
        added_names: Server names for stateless-scan reconstruction.
        added_blocks: Rendered ``"## {name}\\n{instructions}"`` blocks for
            each entry in `added_names`, in the same order.
        removed_names: Server names that were previously announced but are
            no longer connected.
    """

    added_names: list[str] = field(default_factory=list)
    added_blocks: list[str] = field(default_factory=list)
    removed_names: list[str] = field(default_factory=list)


@dataclass
class ClientSideInstruction:
    """Client-authored instruction block to announce when a server connects.

    Supplements (or replaces) the server's own ``InitializeResult.instructions``.
    Enables first-party servers (e.g., claude-in-chrome) to carry client-side
    context the server itself does not know about.
    """

    server_name: str
    block: str


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def is_mcp_instructions_delta_enabled() -> bool:
    """Return True if MCP instructions should be announced via delta attachments.

    Env override for local testing: ``CLAUDE_CODE_MCP_INSTR_DELTA=true/false``
    wins over both the ant bypass and the GrowthBook gate.

    When the gate is **disabled**, ``prompts.ts`` keeps its
    ``DANGEROUS_uncachedSystemPromptSection`` (rebuilt every turn;
    cache-busts on late connect).
    """
    raw = os.environ.get("CLAUDE_CODE_MCP_INSTR_DELTA")
    if is_env_truthy(raw):
        return True
    if is_env_defined_falsy(raw):
        return False
    return os.environ.get("USER_TYPE") == "ant" or _get_feature_value_cached_may_be_stale(
        "tengu_basalt_3kr", False
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def reconstruct_announced_servers(messages: list[Any]) -> set[str]:
    """Walk message history and reconstruct which MCP servers have already
    been announced via ``mcp_instructions_delta`` attachments.

    Returns:
        Set of server names that are currently announced (additions minus
        removals, in message order).
    """
    announced: set[str] = set()
    for msg in messages:
        if not _is_mcp_instructions_delta_message(msg):
            continue
        att = msg.attachment
        for name in getattr(att, "added_names", []) or []:
            announced.add(name)
        for name in getattr(att, "removed_names", []) or []:
            announced.discard(name)
    return announced


def build_server_blocks(
    connected: list[Any],
    client_side_instructions: list[ClientSideInstruction],
    connected_names: set[str],
) -> dict[str, str]:
    """Build a mapping of ``server_name -> rendered instructions block``.

    Merges server-authored ``InitializeResult.instructions`` with any
    matching client-side instruction blocks.

    A server can contribute through either channel (or both).  Client-side
    blocks are appended after the server-authored block with a blank-line
    separator.
    """
    blocks: dict[str, str] = {}

    # Server-authored instructions
    for c in connected:
        name = getattr(c, "name", "")
        if not name:
            continue
        instr = getattr(c, "instructions", None)
        if instr:
            blocks[name] = f"## {name}\n{instr}"

    # Client-side synthesized instructions
    for ci in client_side_instructions:
        if not ci.server_name or not ci.block:
            continue
        if ci.server_name not in connected_names:
            continue
        existing = blocks.get(ci.server_name)
        blocks[ci.server_name] = (
            f"{existing}\n\n{ci.block}"
            if existing
            else f"## {ci.server_name}\n{ci.block}"
        )

    return blocks


def _is_mcp_instructions_delta_message(msg: Any) -> bool:
    """Return True if *msg* is an ``mcp_instructions_delta`` attachment message."""
    if getattr(msg, "type", None) != "attachment":
        return False
    att = getattr(msg, "attachment", None)
    if att is None:
        return False
    return getattr(att, "type", None) == "mcp_instructions_delta"


def _count_attachment_stats(messages: list[Any]) -> tuple[int, int]:
    """Return ``(attachment_count, mid_count)`` for diagnostics.

    ``attachment_count`` — total number of attachment-typed messages.
    ``mid_count`` — subset of those whose attachment type is
    ``mcp_instructions_delta``.
    """
    attachment_count = 0
    mid_count = 0
    for msg in messages:
        if getattr(msg, "type", None) != "attachment":
            continue
        attachment_count += 1
        if _is_mcp_instructions_delta_message(msg):
            mid_count += 1
    return attachment_count, mid_count


# ---------------------------------------------------------------------------
# Core diff
# ---------------------------------------------------------------------------


def get_mcp_instructions_delta(
    mcp_clients: list[Any],
    messages: list[Any],
    client_side_instructions: list[ClientSideInstruction],
) -> McpInstructionsDelta | None:
    """Diff the current set of connected MCP servers that have instructions
    against what has already been announced in this conversation.

    Returns ``None`` when nothing changed.

    Args:
        mcp_clients: Full list of MCP server connections (any status).
        messages: All messages in the conversation so far.
        client_side_instructions: Client-authored instruction blocks to
            synthesize for connected servers.

    Returns:
        An ``McpInstructionsDelta`` describing additions and removals, or
        ``None`` when the set is unchanged.
    """
    # --- Defensive guards ---------------------------------------------------
    if mcp_clients is None:
        return None
    if messages is None:
        messages = []
    if client_side_instructions is None:
        client_side_instructions = []

    # --- Reconstruct announced state from history ---------------------------
    announced = reconstruct_announced_servers(messages)

    # --- Currently connected servers with instructions -----------------------
    connected = [c for c in mcp_clients if getattr(c, "type", None) == "connected"]
    connected_names = {getattr(c, "name", "") for c in connected}
    # Discard empty-string names (malformed connections)
    connected_names.discard("")

    # --- Build the "desired" set of blocks ----------------------------------
    blocks = build_server_blocks(connected, client_side_instructions, connected_names)

    # --- Compute additions --------------------------------------------------
    added: list[tuple[str, str]] = []
    for name, block in blocks.items():
        if name not in announced:
            added.append((name, block))

    # --- Compute removals ---------------------------------------------------
    # A previously-announced server that is no longer connected → removed.
    # There is no "announced but now has no instructions" case for a still-
    # connected server: InitializeResult is immutable, and client-side
    # instruction gates are session-stable in practice. (/model can flip
    # the model gate, but deferred_tools_delta has the same property and
    # we treat history as historical — no retroactive retractions.)
    removed = [n for n in announced if n not in connected_names]

    # --- Early exit when nothing changed ------------------------------------
    if not added and not removed:
        return None

    # --- Diagnostics --------------------------------------------------------
    attachment_count, mid_count = _count_attachment_stats(messages)
    _log_event(
        "tengu_mcp_instructions_pool_change",
        {
            "addedCount": len(added),
            "removedCount": len(removed),
            "priorAnnouncedCount": len(announced),
            "clientSideCount": len(client_side_instructions),
            "messagesLength": len(messages),
            "attachmentCount": attachment_count,
            "midCount": mid_count,
        },
    )

    # --- Build result --------------------------------------------------------
    added.sort(key=lambda x: x[0])
    return McpInstructionsDelta(
        added_names=[a[0] for a in added],
        added_blocks=[a[1] for a in added],
        removed_names=sorted(removed),
    )


# ---------------------------------------------------------------------------
# Attachment builders (for use by attachments.py)
# ---------------------------------------------------------------------------


def get_mcp_instructions_delta_attachment(
    mcp_clients: list[Any],
    messages: list[Any] | None,
    client_side_instructions: list[ClientSideInstruction] | None = None,
) -> list[dict[str, Any]]:
    """Build zero or one ``mcp_instructions_delta`` attachment dicts.

    Convenience wrapper that applies the feature gate, computes the diff,
    and returns a ready-to-append list of attachment dicts (empty when the
    gate is off or nothing changed).

    Returns:
        A list of 0 or 1 attachment dicts with ``type`` set to
        ``"mcp_instructions_delta"``.
    """
    if not is_mcp_instructions_delta_enabled():
        return []

    instructions = client_side_instructions or []
    delta = get_mcp_instructions_delta(mcp_clients, messages or [], instructions)
    if delta is None:
        return []

    return [
        {
            "type": "mcp_instructions_delta",
            "added_names": delta.added_names,
            "added_blocks": delta.added_blocks,
            "removed_names": delta.removed_names,
        }
    ]


def has_unannounced_mcp_instructions(
    mcp_clients: list[Any],
    messages: list[Any],
    client_side_instructions: list[ClientSideInstruction] | None = None,
) -> bool:
    """Return True if there are connected MCP servers with instructions that
    have not yet been announced in the conversation.

    Useful for callers that need to know whether to schedule a delta
    attachment generation without building the full payload.
    """
    if mcp_clients is None:
        return False
    announced = reconstruct_announced_servers(messages or [])
    connected = [c for c in mcp_clients if getattr(c, "type", None) == "connected"]
    connected_names = {getattr(c, "name", "") for c in connected}
    connected_names.discard("")
    blocks = build_server_blocks(
        connected, client_side_instructions or [], connected_names
    )
    for name in blocks:
        if name not in announced:
            return True
    # Also flag if previously-announced servers disappeared
    for name in announced:
        if name not in connected_names:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "McpInstructionsDelta",
    "ClientSideInstruction",
    "is_mcp_instructions_delta_enabled",
    "get_mcp_instructions_delta",
    "get_mcp_instructions_delta_attachment",
    "has_unannounced_mcp_instructions",
    "reconstruct_announced_servers",
    "build_server_blocks",
]
