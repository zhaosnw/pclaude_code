"""
Read/search collapse groups (`collapseReadSearch.ts`).

Full UI merge logic depends on Tool registry and message types; the heavy
`collapse_read_search_groups` path is stubbed until those modules are wired.
Pure helpers below match the TypeScript implementation.
"""

from __future__ import annotations

from typing import Any

# --- Pure summary helpers (complete port) ---


def get_search_read_summary_text(
    search_count: int,
    read_count: int,
    is_active: bool,
    repl_count: int = 0,
    memory_counts: dict[str, int] | None = None,
    list_count: int = 0,
) -> str:
    parts: list[str] = []

    if memory_counts:
        mrc = memory_counts.get("memoryReadCount", 0)
        msc = memory_counts.get("memorySearchCount", 0)
        mwc = memory_counts.get("memoryWriteCount", 0)
        if mrc > 0:
            verb = _verb(
                is_active, parts, "Recalling", "recalling", "Recalled", "recalled"
            )
            parts.append(f"{verb} {mrc} {'memory' if mrc == 1 else 'memories'}")
        if msc > 0:
            verb = _verb(
                is_active, parts, "Searching", "searching", "Searched", "searched"
            )
            parts.append(f"{verb} memories")
        if mwc > 0:
            verb = _verb(is_active, parts, "Writing", "writing", "Wrote", "wrote")
            parts.append(f"{verb} {mwc} {'memory' if mwc == 1 else 'memories'}")

    if search_count > 0:
        sv = _verb(
            is_active,
            parts,
            "Searching for",
            "searching for",
            "Searched for",
            "searched for",
        )
        parts.append(
            f"{sv} {search_count} {'pattern' if search_count == 1 else 'patterns'}"
        )

    if read_count > 0:
        rv = _verb(is_active, parts, "Reading", "reading", "Read", "read")
        parts.append(f"{rv} {read_count} {'file' if read_count == 1 else 'files'}")

    if list_count > 0:
        lv = _verb(is_active, parts, "Listing", "listing", "Listed", "listed")
        parts.append(
            f"{lv} {list_count} {'directory' if list_count == 1 else 'directories'}"
        )

    if repl_count > 0:
        repl_verb = "REPL'ing" if is_active else "REPL'd"
        parts.append(
            f"{repl_verb} {repl_count} {'time' if repl_count == 1 else 'times'}"
        )

    text = ", ".join(parts)
    return f"{text}…" if is_active else text


def _verb(
    is_active: bool,
    parts: list[str],
    a1: str,
    a2: str,
    p1: str,
    p2: str,
) -> str:
    if is_active:
        return a1 if not parts else a2
    return p1 if not parts else p2


def summarize_recent_activities(
    activities: list[dict[str, Any]],
) -> str | None:
    if not activities:
        return None
    search_count = 0
    read_count = 0
    for i in range(len(activities) - 1, -1, -1):
        a = activities[i]
        if a.get("isSearch"):
            search_count += 1
        elif a.get("isRead"):
            read_count += 1
        else:
            break
    if search_count + read_count >= 2:
        return get_search_read_summary_text(search_count, read_count, True)
    for i in range(len(activities) - 1, -1, -1):
        desc = activities[i].get("activityDescription")
        if desc:
            return str(desc)
    return None


def collapse_read_search_groups(messages: list[Any], tools: Any) -> list[Any]:
    """Passthrough until Tool + message types are integrated."""
    return messages


def get_tool_use_ids_from_collapsed_group(message: dict[str, Any]) -> list[str]:
    return []


def has_any_tool_in_progress(_message: dict[str, Any], _in_progress: set[str]) -> bool:
    return False


def get_display_message_from_collapsed(message: dict[str, Any]) -> Any:
    return message.get("displayMessage")
