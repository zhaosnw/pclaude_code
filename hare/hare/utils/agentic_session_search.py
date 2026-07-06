"""Semantic session search via side query (`agenticSessionSearch.ts`)."""

from __future__ import annotations

import re
from typing import Any

from hare.utils.array import count
from hare.utils.debug import log_for_debugging
from hare.utils.log import log_error
from hare.utils.model.model_full import get_small_fast_model
from hare.utils.session_storage import is_lite_log, load_full_log
from hare.utils.side_query import SideQueryOptions, side_query
from hare.utils.slow_operations import json_parse


def _get_log_display_title(log: dict[str, Any], default_title: str = "Untitled") -> str:
    if log.get("customTitle"):
        return str(log["customTitle"])
    fp = log.get("firstPrompt")
    if fp and not str(fp).startswith("<tick>"):
        return str(fp)[:120]
    return default_title


MAX_TRANSCRIPT_CHARS = 2000
MAX_MESSAGES_TO_SCAN = 100
MAX_SESSIONS_TO_SEARCH = 100

SESSION_SEARCH_SYSTEM_PROMPT = """Your goal is to find relevant sessions based on a user's search query.

You will be given a list of sessions with their metadata and a search query. Identify which sessions are most relevant to the query.

Each session may include:
- Title (display name or custom title)
- Tag (user-assigned category, shown as [tag: name] - users tag sessions with /tag command to categorize them)
- Branch (git branch name, shown as [branch: name])
- Summary (AI-generated summary)
- First message (beginning of the conversation)
- Transcript (excerpt of conversation content)

IMPORTANT: Tags are user-assigned labels that indicate the session's topic or category. If the query matches a tag exactly or partially, those sessions should be highly prioritized.

For each session, consider (in order of priority):
1. Exact tag matches (highest priority - user explicitly categorized this session)
2. Partial tag matches or tag-related terms
3. Title matches (custom titles or first message content)
4. Branch name matches
5. Summary and transcript content matches
6. Semantic similarity and related concepts

CRITICAL: Be VERY inclusive in your matching. Include sessions that:
- Contain the query term anywhere in any field
- Are semantically related to the query (e.g., "testing" matches sessions about "tests", "unit tests", "QA", etc.)
- Discuss topics that could be related to the query
- Have transcripts that mention the concept even in passing

When in doubt, INCLUDE the session. It's better to return too many results than too few. The user can easily scan through results, but missing relevant sessions is frustrating.

Return sessions ordered by relevance (most relevant first). If truly no sessions have ANY connection to the query, return an empty array - but this should be rare.

Respond with ONLY the JSON object, no markdown formatting:
{"relevant_indices": [2, 5, 0]}"""


def _extract_message_text(message: dict[str, Any]) -> str:
    if message.get("type") not in ("user", "assistant"):
        return ""
    inner = message.get("message") or {}
    content = inner.get("content") if isinstance(inner, dict) else None
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return " ".join(parts)
    return ""


def _extract_transcript(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return ""
    if len(messages) <= MAX_MESSAGES_TO_SCAN:
        to_scan = messages
    else:
        half = MAX_MESSAGES_TO_SCAN // 2
        to_scan = messages[:half] + messages[-half:]
    text = " ".join(filter(None, (_extract_message_text(m) for m in to_scan)))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_TRANSCRIPT_CHARS:
        return text[:MAX_TRANSCRIPT_CHARS] + "…"
    return text


def _log_contains_query(log: dict[str, Any], query_lower: str) -> bool:
    title = _get_log_display_title(log).lower()
    if query_lower in title:
        return True
    if log.get("customTitle") and query_lower in str(log["customTitle"]).lower():
        return True
    if log.get("tag") and query_lower in str(log["tag"]).lower():
        return True
    if log.get("gitBranch") and query_lower in str(log["gitBranch"]).lower():
        return True
    if log.get("summary") and query_lower in str(log["summary"]).lower():
        return True
    if log.get("firstPrompt") and query_lower in str(log["firstPrompt"]).lower():
        return True
    msgs = log.get("messages")
    if msgs and isinstance(msgs, list) and len(msgs) > 0:
        if query_lower in _extract_transcript(msgs).lower():
            return True
    return False


async def agentic_session_search(
    query: str,
    logs: list[dict[str, Any]],
    signal: Any | None = None,
) -> list[dict[str, Any]]:
    if not query.strip() or not logs:
        return []
    query_lower = query.lower()
    matching = [log for log in logs if _log_contains_query(log, query_lower)]
    if len(matching) >= MAX_SESSIONS_TO_SEARCH:
        logs_to_search = matching[:MAX_SESSIONS_TO_SEARCH]
    else:
        non_matching = [
            log for log in logs if not _log_contains_query(log, query_lower)
        ]
        remaining = MAX_SESSIONS_TO_SEARCH - len(matching)
        logs_to_search = matching + non_matching[:remaining]

    log_for_debugging(
        f"Agentic search: {len(logs_to_search)}/{len(logs)} logs, query={query!r}, "
        f"matching: {len(matching)}, with messages: {count(logs_to_search, lambda log: bool(log.get('messages')))}"
    )

    async def _load(log: dict[str, Any]) -> dict[str, Any]:
        if is_lite_log(log):
            try:
                return await load_full_log(log)
            except Exception as e:
                log_error(e)
                return log
        return log

    import asyncio

    logs_with_transcripts = await asyncio.gather(
        *(_load(log) for log in logs_to_search)
    )
    log_for_debugging(
        f"Agentic search: loaded {count(logs_with_transcripts, lambda log: bool(log.get('messages')))}/{len(logs_to_search)} logs with transcripts"
    )

    parts_out: list[str] = []
    for index, log in enumerate(logs_with_transcripts):
        parts: list[str] = [f"{index}:"]
        display_title = _get_log_display_title(log)
        parts.append(display_title)
        if log.get("customTitle") and log["customTitle"] != display_title:
            parts.append(f"[custom title: {log['customTitle']}]")
        if log.get("tag"):
            parts.append(f"[tag: {log['tag']}]")
        if log.get("gitBranch"):
            parts.append(f"[branch: {log['gitBranch']}]")
        if log.get("summary"):
            parts.append(f"- Summary: {log['summary']}")
        fp = log.get("firstPrompt")
        if fp and fp != "No prompt":
            parts.append(f"- First message: {str(fp)[:300]}")
        msgs = log.get("messages")
        if msgs and isinstance(msgs, list) and len(msgs) > 0:
            tr = _extract_transcript(msgs)
            if tr:
                parts.append(f"- Transcript: {tr}")
        parts_out.append(" ".join(parts))
    session_list = "\n".join(parts_out)
    user_message = f'Sessions:\n{session_list}\n\nSearch query: "{query}"\n\nFind the sessions that are most relevant to this query.'

    log_for_debugging(
        f"Agentic search prompt (first 500 chars): {user_message[:500]}..."
    )
    try:
        model = get_small_fast_model()
        log_for_debugging(f"Agentic search using model: {model}")
        response = await side_query(
            SideQueryOptions(
                model=model,
                system=SESSION_SEARCH_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                signal=signal,
                query_source="session_search",
            )
        )
        content = getattr(response, "content", None) if response is not None else None
        if content is None and isinstance(response, dict):
            content = response.get("content")
        blocks = content or []
        text_block = next((b for b in blocks if b.get("type") == "text"), None)
        if not text_block:
            log_for_debugging("No text content in agentic search response")
            return []
        text = text_block.get("text", "")
        log_for_debugging(f"Agentic search response: {text}")
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            log_for_debugging("Could not find JSON in agentic search response")
            return []
        result = json_parse(m.group(0))
        indices = result.get("relevant_indices") or []
        out: list[dict[str, Any]] = []
        for i in indices:
            if isinstance(i, int) and 0 <= i < len(logs_with_transcripts):
                out.append(logs_with_transcripts[i])
        log_for_debugging(f"Agentic search found {len(out)} relevant sessions")
        return out
    except Exception as e:
        log_error(e)
        log_for_debugging(f"Agentic search error: {e}")
        return []
