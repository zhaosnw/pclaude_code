"""Port of: src/utils/sessionStorage.ts

Session file I/O, transcript loading, chain walking, and metadata management.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hare.utils.log import log_error

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_transcript_base = Path.home() / ".hare" / "transcripts"
_projects_base = Path.home() / ".hare" / "projects"

# Session-scoped file pointer (lazy-materialized per session switch)
_session_file: Path | None = None
_session_file_msg_cache: set[str] | None = None


def get_transcript_path(session_id: str) -> str:
    return str(_transcript_base / f"{session_id}.jsonl")


def get_project_dir(cwd: str | None = None) -> str:
    """Memoized mapping: cwd -> ~/.hare/projects/<sanitized-name>."""
    if cwd is None:
        from hare.utils.cwd import get_cwd as _get_cwd

        cwd = _get_cwd()
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]", "_", cwd.replace("/", "_"))
    return str(_projects_base / sanitized)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LogOption:
    """A session log entry — metadata + optionally full messages."""

    date: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    full_path: str | None = None
    value: int = 0
    created: datetime | None = None
    modified: datetime | None = None
    first_prompt: str = ""
    message_count: int = 0
    is_sidechain: bool = False
    session_id: str | None = None
    leaf_uuid: str | None = None
    summary: str | None = None
    custom_title: str | None = None
    tag: str | None = None
    agent_name: str | None = None
    agent_color: str | None = None
    agent_setting: str | None = None
    mode: str | None = None
    worktree_session: dict[str, Any] | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    pr_repository: str | None = None
    git_branch: str | None = None
    project_path: str | None = None
    file_history_snapshots: list[Any] | None = None
    attribution_snapshots: list[Any] | None = None
    content_replacements: list[Any] | None = None
    context_collapse_commits: list[Any] | None = None
    context_collapse_snapshot: dict[str, Any] | None = None
    team_name: str | None = None
    file_size: int = 0
    is_lite: bool = False


# ---------------------------------------------------------------------------
# TranscriptMessage helpers
# ---------------------------------------------------------------------------

_TRANSCRIPT_TYPES = {"user", "assistant", "attachment", "system"}


def is_transcript_message(entry: dict[str, Any]) -> bool:
    return entry.get("type") in _TRANSCRIPT_TYPES


def is_legacy_progress_entry(entry: dict[str, Any]) -> bool:
    return entry.get("type") == "progress" and "uuid" in entry and "parentUuid" in entry


# ---------------------------------------------------------------------------
# load_transcript_file — core JSONL parser
# ---------------------------------------------------------------------------


def load_transcript_file(
    file_path: str,
    keep_all_leaves: bool = False,
) -> dict[str, Any]:
    """Parse a session JSONL file into messages and metadata maps.

    Returns a dict with:
        messages: dict[uuid, TranscriptMessage]
        summaries, custom_titles, tags, agent_names, agent_colors,
        agent_settings, modes, pr_numbers, pr_urls, pr_repositories,
        worktree_states, file_history_snapshots, attribution_snapshots,
        content_replacements, context_collapse_commits,
        context_collapse_snapshot, leaf_uuids: set[str]
    """
    messages: dict[str, dict[str, Any]] = {}
    summaries: dict[str, str] = {}
    custom_titles: dict[str, str] = {}
    tags: dict[str, str] = {}
    agent_names: dict[str, str] = {}
    agent_colors: dict[str, str] = {}
    agent_settings: dict[str, str] = {}
    modes: dict[str, str] = {}
    pr_numbers: dict[str, int] = {}
    pr_urls: dict[str, str] = {}
    pr_repositories: dict[str, str] = {}
    worktree_states: dict[str, dict | None] = {}
    file_history_snapshots: dict[str, Any] = {}
    attribution_snapshots: dict[str, Any] = {}
    content_replacements: dict[str, list] = {}
    context_collapse_commits: list[dict] = []
    context_collapse_snapshot: dict | None = None
    leaf_uuids: set[str] = set()

    p = Path(file_path)
    if not p.exists():
        return {
            "messages": messages,
            "summaries": summaries,
            "custom_titles": custom_titles,
            "tags": tags,
            "agent_names": agent_names,
            "agent_colors": agent_colors,
            "agent_settings": agent_settings,
            "modes": modes,
            "pr_numbers": pr_numbers,
            "pr_urls": pr_urls,
            "pr_repositories": pr_repositories,
            "worktree_states": worktree_states,
            "file_history_snapshots": file_history_snapshots,
            "attribution_snapshots": attribution_snapshots,
            "content_replacements": content_replacements,
            "context_collapse_commits": context_collapse_commits,
            "context_collapse_snapshot": context_collapse_snapshot,
            "leaf_uuids": leaf_uuids,
        }

    # Progress bridge: for legacy transcripts with progress entries in the
    # parentUuid chain, rewrite parentUuid to skip over progress gaps.
    progress_bridge: dict[str, str | None] = {}

    # Referent set: all UUIDs that appear as some message's parentUuid
    child_uuids: set[str] = set()

    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

            # Legacy progress bridging
            if is_legacy_progress_entry(entry):
                puuid = entry.get("parentUuid")
                progress_bridge[entry["uuid"]] = (
                    progress_bridge.get(puuid, puuid)
                    if puuid and puuid in progress_bridge
                    else puuid
                )
                continue

            if is_transcript_message(entry):
                # Rewrite parentUuid through progress bridge
                puuid = entry.get("parentUuid")
                if puuid and puuid in progress_bridge:
                    entry["parentUuid"] = progress_bridge[puuid]

                messages[entry["uuid"]] = entry
                if puuid:
                    child_uuids.add(puuid)

                # Compact boundary clears collapse state
                if _is_compact_boundary_entry(entry):
                    context_collapse_commits.clear()
                    context_collapse_snapshot = None

            elif entry.get("type") == "summary" and entry.get("leafUuid"):
                summaries[entry["leafUuid"]] = entry.get("summary", "")
            elif entry.get("type") == "custom-title" and entry.get("sessionId"):
                custom_titles[entry["sessionId"]] = entry.get("customTitle", "")
            elif entry.get("type") == "tag" and entry.get("sessionId"):
                tags[entry["sessionId"]] = entry.get("tag", "")
            elif entry.get("type") == "agent-name" and entry.get("sessionId"):
                agent_names[entry["sessionId"]] = entry.get("agentName", "")
            elif entry.get("type") == "agent-color" and entry.get("sessionId"):
                agent_colors[entry["sessionId"]] = entry.get("agentColor", "")
            elif entry.get("type") == "agent-setting" and entry.get("sessionId"):
                agent_settings[entry["sessionId"]] = entry.get("agentSetting", "")
            elif entry.get("type") == "mode" and entry.get("sessionId"):
                modes[entry["sessionId"]] = entry.get("mode", "")
            elif entry.get("type") == "worktree-state" and entry.get("sessionId"):
                worktree_states[entry["sessionId"]] = entry.get("worktreeSession")
            elif entry.get("type") == "pr-link" and entry.get("sessionId"):
                pr_numbers[entry["sessionId"]] = entry.get("prNumber", 0)
                pr_urls[entry["sessionId"]] = entry.get("prUrl", "")
                pr_repositories[entry["sessionId"]] = entry.get("prRepository", "")
            elif entry.get("type") == "file-history-snapshot" and entry.get(
                "messageId"
            ):
                file_history_snapshots[entry["messageId"]] = entry
            elif entry.get("type") == "attribution-snapshot":
                attribution_snapshots[entry.get("leafUuid", entry.get("uuid", ""))] = (
                    entry
                )
            elif entry.get("type") == "content-replacement" and entry.get("sessionId"):
                sid = entry["sessionId"]
                if sid not in content_replacements:
                    content_replacements[sid] = []
                content_replacements[sid].append(entry)
            elif entry.get("type") == "context-collapse-commit":
                context_collapse_commits.append(entry)
            elif entry.get("type") == "context-collapse-snapshot":
                context_collapse_snapshot = entry

    # Compute leaf UUIDs: UUIDs in messages that are NOT any other message's parentUuid
    all_uuids = set(messages.keys())
    leaf_uuids = all_uuids - child_uuids
    # Also add terminal messages (last in file) if child walk missed them
    if not leaf_uuids and messages:
        # Fallback: last message is the leaf
        last_key = list(messages.keys())[-1]
        leaf_uuids = {last_key}

    return {
        "messages": messages,
        "summaries": summaries,
        "custom_titles": custom_titles,
        "tags": tags,
        "agent_names": agent_names,
        "agent_colors": agent_colors,
        "agent_settings": agent_settings,
        "modes": modes,
        "pr_numbers": pr_numbers,
        "pr_urls": pr_urls,
        "pr_repositories": pr_repositories,
        "worktree_states": worktree_states,
        "file_history_snapshots": file_history_snapshots,
        "attribution_snapshots": attribution_snapshots,
        "content_replacements": content_replacements,
        "context_collapse_commits": context_collapse_commits,
        "context_collapse_snapshot": context_collapse_snapshot,
        "leaf_uuids": leaf_uuids,
    }


def _is_compact_boundary_entry(entry: dict[str, Any]) -> bool:
    return entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"


# ---------------------------------------------------------------------------
# build_conversation_chain
# ---------------------------------------------------------------------------


def build_conversation_chain(
    messages: dict[str, dict[str, Any]],
    leaf_message: dict[str, Any],
) -> list[dict[str, Any]]:
    """Walk parentUuid chain from leaf back to root, then reverse.
    Recovers orphaned parallel tool results from sibling assistants."""
    transcript: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: dict[str, Any] | None = leaf_message

    while current is not None:
        if current["uuid"] in seen:
            log_error(
                f"Cycle detected in parentUuid chain at {current['uuid']}. "
                "Returning partial transcript."
            )
            break
        seen.add(current["uuid"])
        transcript.append(current)
        puuid = current.get("parentUuid")
        current = messages.get(puuid) if puuid else None

    transcript.reverse()
    return _recover_orphaned_parallel_tool_results(messages, transcript, seen)


def _recover_orphaned_parallel_tool_results(
    messages: dict[str, dict[str, Any]],
    chain: list[dict[str, Any]],
    seen: set[str],
) -> list[dict[str, Any]]:
    """Post-pass: recover sibling assistant blocks and their tool_results
    that the single-parent walk orphaned (parallel tool use scenario)."""

    chain_assistants = [m for m in chain if m.get("type") == "assistant"]

    if not chain_assistants:
        return chain

    # Anchor = last on-chain member of each sibling group
    anchor_by_msg_id: dict[str, dict[str, Any]] = {}
    for a in chain_assistants:
        msg_id = (a.get("message") or {}).get("id")
        if msg_id:
            anchor_by_msg_id[msg_id] = a

    # Precompute sibling groups and TR index
    siblings_by_msg_id: dict[str, list[dict[str, Any]]] = {}
    tool_results_by_asst: dict[str, list[dict[str, Any]]] = {}

    for m in messages.values():
        if m.get("type") == "assistant":
            msg_id = (m.get("message") or {}).get("id")
            if msg_id:
                group = siblings_by_msg_id.get(msg_id)
                if group is not None:
                    group.append(m)
                else:
                    siblings_by_msg_id[msg_id] = [m]
        elif m.get("type") == "user":
            puuid = m.get("parentUuid")
            content = (m.get("message") or {}).get("content")
            if puuid and isinstance(content, list):
                if any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                ):
                    group = tool_results_by_asst.get(puuid)
                    if group is not None:
                        group.append(m)
                    else:
                        tool_results_by_asst[puuid] = [m]

    # For each message.id group touching the chain: collect off-chain siblings + TRs
    processed_groups: set[str] = set()
    inserts: dict[str, list[dict[str, Any]]] = {}
    recovered_count = 0

    for asst in chain_assistants:
        msg_id = (asst.get("message") or {}).get("id")
        if not msg_id or msg_id in processed_groups:
            continue
        processed_groups.add(msg_id)

        group = siblings_by_msg_id.get(msg_id, [asst])
        orphaned_siblings = [s for s in group if s["uuid"] not in seen]
        orphaned_trs: list[dict[str, Any]] = []

        for member in group:
            trs = tool_results_by_asst.get(member["uuid"])
            if trs:
                for tr in trs:
                    if tr["uuid"] not in seen:
                        orphaned_trs.append(tr)

        if not orphaned_siblings and not orphaned_trs:
            continue

        # Sort by timestamp
        orphaned_siblings.sort(key=lambda x: x.get("timestamp", ""))
        orphaned_trs.sort(key=lambda x: x.get("timestamp", ""))

        anchor = anchor_by_msg_id.get(msg_id)
        if anchor is None:
            continue

        recovered = orphaned_siblings + orphaned_trs
        for r in recovered:
            seen.add(r["uuid"])
        recovered_count += len(recovered)
        inserts.setdefault(anchor["uuid"], []).extend(recovered)

    if recovered_count == 0:
        return chain

    result: list[dict[str, Any]] = []
    for m in chain:
        result.append(m)
        extra = inserts.get(m["uuid"])
        if extra:
            result.extend(extra)
    return result


# ---------------------------------------------------------------------------
# remove_extra_fields
# ---------------------------------------------------------------------------


def remove_extra_fields(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip isSidechain and parentUuid from transcript messages for serialization."""
    result: list[dict[str, Any]] = []
    for msg in transcript:
        cleaned = {
            k: v for k, v in msg.items() if k not in ("isSidechain", "parentUuid")
        }
        result.append(cleaned)
    return result


# ---------------------------------------------------------------------------
# Session ID / log helpers
# ---------------------------------------------------------------------------


def get_session_id_from_log(log: dict[str, Any]) -> str | None:
    """Extract session_id from a LogOption dict."""
    # Direct field
    sid = log.get("sessionId") or log.get("session_id")
    if isinstance(sid, str) and sid:
        return sid
    # Fall back to first message
    msgs = log.get("messages") or []
    if msgs:
        return msgs[0].get("sessionId")
    return None


def is_lite_log(log: dict[str, Any]) -> bool:
    """Check if a LogOption is a lite log (needs full loading).
    Lite logs have empty messages but a known sessionId."""
    if log.get("isLite") or log.get("is_lite") or log.get("type") == "lite":
        return True
    return len(log.get("messages") or []) == 0 and bool(
        log.get("sessionId") or log.get("session_id")
    )


def load_full_log(log_or_session_id: dict[str, Any] | str) -> dict[str, Any]:
    """Load full messages for a lite log or session_id.

    If given a dict, checks if it's lite and loads if needed.
    If given a session_id string, loads the transcript directly.
    Returns the enriched log dict."""
    if isinstance(log_or_session_id, str):
        session_id = log_or_session_id
        path = get_transcript_path(session_id)
        data = load_transcript_file(path)
        return _enrich_log_from_data(data, session_id, path)

    log = log_or_session_id
    if not is_lite_log(log):
        return log

    session_file = log.get("fullPath") or log.get("full_path")
    if not session_file:
        sid = get_session_id_from_log(log)
        if sid:
            session_file = get_transcript_path(sid)
    if not session_file:
        return log

    try:
        sid = get_session_id_from_log(log)
        data = load_transcript_file(session_file)
        return _enrich_log_from_data(data, sid or "", session_file, log)
    except Exception:
        return log


def _enrich_log_from_data(
    data: dict[str, Any],
    session_id: str,
    file_path: str,
    base_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an enriched LogOption dict from load_transcript_file output."""
    messages = data["messages"]
    leaf_uuids: set[str] = data.get("leafUuids") if "leafUuids" in data else data.get("leaf_uuids", set())

    if not messages:
        return base_log or {
            "messages": [],
            "sessionId": session_id,
            "fullPath": file_path,
        }

    # Find most recent non-sidechain leaf
    most_recent_leaf = None
    most_recent_ts = ""
    for uuid, msg in messages.items():
        if uuid not in leaf_uuids:
            continue
        if msg.get("isSidechain"):
            continue
        ts = msg.get("timestamp", "")
        if ts > most_recent_ts:
            most_recent_ts = ts
            most_recent_leaf = msg

    if most_recent_leaf is None:
        # Fallback: last message
        keys = list(messages.keys())
        most_recent_leaf = messages[keys[-1]] if keys else None

    if most_recent_leaf is None:
        return base_log or {
            "messages": [],
            "sessionId": session_id,
            "fullPath": file_path,
        }

    chain = build_conversation_chain(messages, most_recent_leaf)
    cleaned = remove_extra_fields(chain)

    result = {
        **(base_log or {}),
        "messages": cleaned,
        "sessionId": session_id,
        "fullPath": file_path,
        "firstPrompt": _extract_first_prompt(chain),
        "messageCount": len(
            [m for m in chain if m.get("type") in ("user", "assistant")]
        ),
        "summary": data["summaries"].get(most_recent_leaf["uuid"]),
        "customTitle": data["custom_titles"].get(session_id),
        "tag": data["tags"].get(session_id),
        "agentName": data["agent_names"].get(session_id),
        "agentColor": data["agent_colors"].get(session_id),
        "agentSetting": data["agent_settings"].get(session_id),
        "mode": data["modes"].get(session_id),
        "worktreeSession": data["worktree_states"].get(session_id),
        "prNumber": data["pr_numbers"].get(session_id),
        "prUrl": data["pr_urls"].get(session_id),
        "prRepository": data["pr_repositories"].get(session_id),
        "gitBranch": most_recent_leaf.get("gitBranch"),
        "isSidechain": chain[0].get("isSidechain") if chain else False,
        "teamName": chain[0].get("teamName") if chain else None,
        "leafUuid": most_recent_leaf.get("uuid"),
        "contextCollapseCommits": [
            e
            for e in data["context_collapse_commits"]
            if e.get("sessionId") == session_id
        ],
        "contextCollapseSnapshot": data["context_collapse_snapshot"],
    }
    return result


# ---------------------------------------------------------------------------
# get_last_session_log
# ---------------------------------------------------------------------------


def get_last_session_log(session_id: str) -> dict[str, Any] | None:
    """Load session data, build chain, return enriched LogOption."""
    path = get_transcript_path(session_id)
    if not os.path.isfile(path):
        return None
    data = load_transcript_file(path)
    return _enrich_log_from_data(data, session_id, path)


# ---------------------------------------------------------------------------
# load_message_logs — list sessions
# ---------------------------------------------------------------------------


def load_message_logs(limit: int | None = None) -> list[dict[str, Any]]:
    """Load lite logs for the current project, enriched and sorted."""
    project_dir = get_project_dir()
    files = _get_session_files_lite(project_dir, limit)
    enriched = _enrich_logs(files, 0, len(files))
    logs = enriched["logs"]
    logs.sort(key=lambda log: log.get("modified") or datetime.min, reverse=True)
    # Re-number
    for i, log in enumerate(logs):
        log["value"] = i + 1
    return _deduplicate_logs_by_session_id(logs)


def _get_session_files_lite(
    project_dir: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return lite LogOption dicts from session file stats."""
    files = _get_session_files_with_mtime(project_dir)
    # Sort by mtime descending
    sorted_files = sorted(
        files.items(),
        key=lambda kv: kv[1].get("mtime", 0.0),
        reverse=True,
    )
    if limit is not None:
        sorted_files = sorted_files[:limit]

    result: list[dict[str, Any]] = []
    for session_id, info in sorted_files:
        result.append(
            {
                "sessionId": session_id,
                "fullPath": info["path"],
                "isLite": True,
                "messages": [],
                "modified": info.get("mtime_dt"),
                "created": info.get("ctime_dt"),
                "fileSize": info.get("size", 0),
            }
        )
    return result


def _get_session_files_with_mtime(project_dir: str) -> dict[str, dict[str, Any]]:
    """Stat all .jsonl files in the project directory."""
    result: dict[str, dict[str, Any]] = {}
    base = Path(project_dir)
    if not base.exists():
        return result

    for p in base.glob("*.jsonl"):
        session_id = p.stem  # filename without .jsonl
        try:
            stat = p.stat()
            mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            ctime_dt = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
            result[session_id] = {
                "path": str(p),
                "mtime": stat.st_mtime,
                "mtime_dt": mtime_dt,
                "ctime_dt": ctime_dt,
                "size": stat.st_size,
            }
        except OSError:
            pass
    return result


def _enrich_logs(
    logs: list[dict[str, Any]],
    start_index: int,
    count: int,
) -> dict[str, Any]:
    """Enrich lite logs with firstPrompt/gitBranch/customTitle/tag."""
    end = min(start_index + count, len(logs))
    enriched: list[dict[str, Any]] = []
    for i in range(start_index, end):
        log = logs[i]
        enriched_log = _enrich_log(log)
        if enriched_log is not None:
            enriched.append(enriched_log)
    return {"logs": enriched, "nextIndex": end}


def _enrich_log(log: dict[str, Any]) -> dict[str, Any] | None:
    """Enrich a single lite log with metadata from head/tail reads."""
    path = log.get("fullPath")
    if not path:
        return log

    p = Path(path)
    if not p.exists():
        return None

    try:
        file_size = p.stat().st_size
        log["fileSize"] = file_size

        # Read tail for recent metadata
        with p.open("r", encoding="utf-8") as f:
            # Seek to last ~16KB
            tail_size = min(file_size, 16384)
            if file_size > tail_size:
                f.seek(file_size - tail_size)
            tail_lines = f.readlines()

        # Extract metadata from tail
        for line in reversed(tail_lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

            t = entry.get("type")
            if t == "custom-title" and not log.get("customTitle"):
                log["customTitle"] = entry.get("customTitle")
            elif t == "tag" and not log.get("tag"):
                log["tag"] = entry.get("tag")
            elif t == "agent-name" and not log.get("agentName"):
                log["agentName"] = entry.get("agentName")
            elif t == "agent-color" and not log.get("agentColor"):
                log["agentColor"] = entry.get("agentColor")
            elif t == "summary" and not log.get("summary"):
                log["summary"] = entry.get("summary")
            elif t == "git-branch" and not log.get("gitBranch"):
                log["gitBranch"] = entry.get("gitBranch")
            elif not log.get("firstPrompt") and t == "user":
                content = (entry.get("message") or {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    # Check it's not meta
                    if not entry.get("isMeta"):
                        log["firstPrompt"] = content.strip()[:200]

        # Read head for first prompt if not found in tail
        if not log.get("firstPrompt"):
            with p.open("r", encoding="utf-8") as f:
                for _ in range(100):
                    line = f.readline()
                    if not line:
                        break
                    try:
                        entry = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict) and entry.get("type") == "user":
                        content = (entry.get("message") or {}).get("content", "")
                        if isinstance(content, str) and content.strip():
                            if not entry.get("isMeta"):
                                log["firstPrompt"] = content.strip()[:200]
                                break

        # Filter sidechain logs
        if log.get("isSidechain") or log.get("teamName"):
            return None

        return log
    except Exception:
        return log


def _deduplicate_logs_by_session_id(
    logs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for log in logs:
        sid = get_session_id_from_log(log)
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        result.append(log)
    return result


# ---------------------------------------------------------------------------
# extract_first_prompt
# ---------------------------------------------------------------------------


def _extract_first_prompt(transcript: list[dict[str, Any]]) -> str:
    for m in transcript:
        if m.get("type") != "user":
            continue
        if m.get("isMeta"):
            continue
        content = (m.get("message") or {}).get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()[:200]
        if isinstance(content, list):
            text = "".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if text.strip():
                return text.strip()[:200]
    return ""


# ---------------------------------------------------------------------------
# check_resume_consistency
# ---------------------------------------------------------------------------


def check_resume_consistency(chain: list[dict[str, Any]]) -> None:
    """Compare turn_duration checkpoint against chain position.

    Emits a log event with delta between expected and actual message count
    for monitoring resume round-trip consistency."""
    for i in range(len(chain) - 1, -1, -1):
        m = chain[i]
        if m.get("type") != "system":
            continue
        if m.get("subtype") != "turn_duration":
            continue
        meta = m.get("compact_metadata") or {}
        td = meta.get("turnDuration") or {}
        expected = td.get("messageCount")
        if expected is None:
            return
        actual = i
        log_error(
            f"resume_consistency_delta: expected={expected}, actual={actual}, "
            f"delta={actual - expected}, chain_length={len(chain)}"
        )
        return


# ---------------------------------------------------------------------------
# record_transcript — write path
# ---------------------------------------------------------------------------


def message_to_transcript_entry(
    message: Any,
    *,
    parent_uuid: str | None,
    session_id: str,
    cwd: str = "",
) -> dict[str, Any]:
    """Serialize a live in-memory message into a camelCase transcript JSONL entry
    that ``load_transcript_file`` reads back, maintaining the parentUuid chain.

    Accepts a Message dataclass (live turn) or an already transcript-shaped dict
    (resumed session, re-recorded into the new session file)."""
    from uuid import uuid4

    # Resumed sessions seed plain envelope dicts; pass through, only ensuring the
    # keys the reader/chain-walker need are present.
    if isinstance(message, dict):
        entry = dict(message)
        entry.setdefault("uuid", str(uuid4()))
        entry.setdefault("sessionId", session_id)
        entry.setdefault("parentUuid", parent_uuid)
        return entry

    api = getattr(message, "message", None)
    if api is not None and not isinstance(api, dict):
        message_payload: dict[str, Any] = {
            "role": getattr(api, "role", ""),
            "content": getattr(api, "content", ""),
        }
    else:
        message_payload = api or {}

    return {
        "type": getattr(message, "type", ""),
        "uuid": getattr(message, "uuid", "") or str(uuid4()),
        "parentUuid": parent_uuid,
        "sessionId": session_id,
        "message": message_payload,
        "isSidechain": False,
        "isMeta": bool(getattr(message, "is_meta", False)),
        "cwd": cwd,
        "timestamp": getattr(message, "timestamp", "")
        or datetime.now(timezone.utc).isoformat(),
    }


def record_transcript(
    messages: list[dict[str, Any]],
    team_info: dict[str, Any] | None = None,
    starting_parent_uuid_hint: str | None = None,
    all_messages: list[dict[str, Any]] | None = None,
) -> str | None:
    """Write new messages to the session JSONL file.

    Deduplicates against the in-memory message cache.
    Returns the UUID of the last recorded chain participant."""
    global _session_file, _session_file_msg_cache

    if _session_file is None:
        from hare.bootstrap.state import get_session_id

        sid = get_session_id()
        if sid:
            _session_file = Path(get_transcript_path(sid))
            _session_file_msg_cache = _load_existing_uuids(_session_file)

    if _session_file is None:
        return None

    if _session_file_msg_cache is None:
        _session_file_msg_cache = _load_existing_uuids(_session_file)

    # Filter already-recorded messages
    new_msgs = [
        m
        for m in messages
        if m.get("uuid") and m["uuid"] not in _session_file_msg_cache
    ]

    if not new_msgs:
        return None

    # Append to JSONL
    _session_file.parent.mkdir(parents=True, exist_ok=True)
    last_uuid: str | None = None
    with _session_file.open("a", encoding="utf-8") as f:
        for msg in new_msgs:
            f.write(json.dumps(msg, default=str) + "\n")
            uuid = msg.get("uuid")
            if uuid:
                _session_file_msg_cache.add(uuid)
                if _is_chain_participant(msg):
                    last_uuid = uuid

    return last_uuid


def _load_existing_uuids(path: Path) -> set[str]:
    """Load all existing message UUIDs from a JSONL file."""
    uuids: set[str] = set()
    if not path.exists():
        return uuids
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("uuid"):
                uuids.add(entry["uuid"])
    return uuids


def _is_chain_participant(msg: dict[str, Any]) -> bool:
    return msg.get("type") != "progress"


def record_sidechain_transcript(
    session_id: str,
    data: dict[str, Any],
    agent_id: str | None = None,
    starting_parent_uuid: str | None = None,
) -> None:
    """Write a sidechain entry to the session (or agent) JSONL file."""
    path = get_transcript_path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, default=str) + "\n")


# ---------------------------------------------------------------------------
# adopt_resumed_session_file
# ---------------------------------------------------------------------------


def adopt_resumed_session_file() -> None:
    """Set the session file pointer after --continue/--resume."""
    global _session_file, _session_file_msg_cache
    from hare.bootstrap.state import get_session_id

    sid = get_session_id()
    if sid:
        _session_file = Path(get_transcript_path(sid))
        _session_file_msg_cache = _load_existing_uuids(_session_file)
        re_append_session_metadata()


def reset_session_file_pointer() -> None:
    """Reset session file pointer after switchSession."""
    global _session_file, _session_file_msg_cache
    _session_file = None
    _session_file_msg_cache = None


# ---------------------------------------------------------------------------
# Metadata write helpers (stubs for now)
# ---------------------------------------------------------------------------


_session_metadata_cache: dict[str, Any] = {}


def re_append_session_metadata() -> None:
    """Re-append cached metadata after resume/compaction."""
    global _session_file
    if _session_file is None or not _session_metadata_cache:
        return
    _session_file.parent.mkdir(parents=True, exist_ok=True)
    with _session_file.open("a", encoding="utf-8") as f:
        for item in _session_metadata_cache.values():
            f.write(json.dumps(item, default=str) + "\n")


def clear_session_messages_cache() -> None:
    """Clear memoized message UUID cache after compaction."""
    global _session_file_msg_cache
    _session_file_msg_cache = None


def set_agent_transcript_subdir(agent_id: str) -> None:
    pass


def clear_agent_transcript_subdir() -> None:
    pass


def write_agent_metadata(agent_id: str, metadata: dict[str, Any]) -> None:
    pass
