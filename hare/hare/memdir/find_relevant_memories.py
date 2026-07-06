"""Select relevant memories via side query or local scoring (port of src/memdir/findRelevantMemories.ts).

When the side-query path is available (LLM-based selection), it mirrors the TS
implementation: format a memory manifest, ask Sonnet to select up to 5 relevant
files, and return them.  When side-query is not wired up, the module falls back
to a local relevance-scoring engine that ranks memories by query-document
similarity, metadata matches, recency, and tool-based signals.

Exports
-------
- RelevantMemory:    dataclass with path, mtime_ms, score, match_reasons
- find_relevant_memories():  async entry point (same signature as TS)
- search_memories():         full-text search across memory files
- score_memory_relevance():  pure scoring function (sync, testable)
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from hare.memdir.memory_age import memory_age_days
from hare.memdir.memory_scan import (
    MemoryHeader,
    format_memory_manifest,
    scan_memory_files,
)
from hare.utils.debug import log_for_debugging
from hare.utils.errors import error_message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SELECTED_MEMORIES = 5
MAX_CONTENT_READ_BYTES = 8_192  # read at most 8 KB per memory file
RECENCY_HALF_LIFE_DAYS = 30.0   # half-life for exponential recency decay
TOOL_BOOST_WEIGHT = 0.12        # max boost from tool mentions
DESCRIPTION_BOOST = 0.15        # boost for description matches
FILENAME_BOOST = 0.08           # boost for filename matches
TYPE_BOOST_MAP: dict[str | None, float] = {
    "project": 0.05,
    "feedback": 0.03,
    "user": 0.02,
    "reference": 0.01,
    None: 0.0,
}

# Minimum content-match score for a candidate to be eligible when
# the query has non-trivial content terms (avoids returning unrelated
# files when the query is substantive).
MIN_CONTENT_SCORE_THRESHOLD = 0.05

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RelevantMemory:
    """A memory file deemed relevant to a query.

    Attributes
    ----------
    path:
        Absolute filesystem path to the memory ``.md`` file.
    mtime_ms:
        Modification time in milliseconds since Unix epoch.
    score:
        Relevance score (0.0–1.0).  Higher is more relevant.
    match_reasons:
        Human-readable tags explaining why this memory was selected
        (e.g. ``"description:login"``, ``"content:auth"``, ``"tool:bash"``).
    """

    path: str
    mtime_ms: float
    score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """Result from ``search_memories()``."""

    path: str
    mtime_ms: float
    score: float
    match_reasons: list[str] = field(default_factory=list)
    snippet: str = ""  # first matching line / excerpt (≤ 200 chars)


# ---------------------------------------------------------------------------
# Query tokenization
# ---------------------------------------------------------------------------

# Words shorter than this are discarded as noise.
_MIN_TOKEN_LEN = 2

# Common stop-words removed before scoring.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "yet",
        "both",
        "either",
        "neither",
        "each",
        "every",
        "all",
        "any",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "about",
        "also",
        "if",
        "then",
        "else",
        "when",
        "where",
        "why",
        "how",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "he",
        "she",
        "they",
        "them",
        "their",
        "we",
        "you",
        "i",
        "me",
        "my",
        "your",
        "our",
        "what",
        "which",
        "who",
        "whom",
    }
)


def _tokenize(text: str) -> list[str]:
    """Lower-case, split on non-alphanum, drop stop-words and short tokens."""
    raw = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return [t for t in raw if len(t) >= _MIN_TOKEN_LEN and t not in _STOP_WORDS]


def _extract_phrases(text: str, max_phrase_len: int = 3) -> list[str]:
    """Extract short n-gram phrases (bigrams, trigrams) for exact matching."""
    tokens = _tokenize(text)
    phrases: list[str] = []
    for n in (2, 3):
        if len(tokens) < n:
            break
        for i in range(len(tokens) - n + 1):
            phrase = " ".join(tokens[i : i + n])
            if len(phrase) <= max_phrase_len * 12:  # sanity cap
                phrases.append(phrase)
    return phrases


# ---------------------------------------------------------------------------
# Content reading
# ---------------------------------------------------------------------------


def _read_memory_body(file_path: str, max_bytes: int = MAX_CONTENT_READ_BYTES) -> str:
    """Read the body of a memory file (skipping YAML frontmatter).

    Returns at most *max_bytes* of content.  Gracefully returns ``""`` on
    any I/O error or when the file appears to be binary (null bytes).

    Edge cases handled:
    - File does not exist or is a directory → ``""``
    - Permission denied → ``""``
    - File appears binary (contains null bytes in first chunk) → ``""``
    - Empty file → ``""``
    - Frontmatter-only file (no body after ``---``) → ``""``
    """
    try:
        if not os.path.isfile(file_path):
            return ""
        with open(file_path, encoding="utf-8") as fh:
            raw = fh.read(max_bytes + 512)  # small over-read for frontmatter skip
    except (OSError, UnicodeDecodeError):
        return ""

    # Binary-file guard: if null bytes present in the first 1 KB, treat as binary
    if "\x00" in raw[:1024]:
        return ""

    # Skip YAML frontmatter (--- ... ---)
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
        else:
            body = raw
    else:
        body = raw

    return body[:max_bytes]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _compute_content_score(query_tokens: list[str], content: str) -> float:
    """Score a document body against query tokens (TF-like, length-normalised).

    Returns a value in [0.0, 1.0].

    Edge cases:
    - Empty tokens or content → 0.0
    - All tokens are stop-word-only → 0.0
    - Whitespace-only content → 0.0
    - Document much shorter than query → penalised
    """
    if not query_tokens:
        return 0.0
    if not content or not content.strip():
        return 0.0

    content_lower = content.lower()
    hits = sum(1 for t in query_tokens if t in content_lower)
    raw = hits / len(query_tokens)

    # Mild length normalisation: very short documents that match all tokens
    # are penalised slightly (they might be trivial matches).
    doc_len = len(content_lower)
    if doc_len < 200:
        raw *= min(1.0, doc_len / 200.0)

    return min(raw, 1.0)


def _compute_phrase_score(query_phrases: list[str], content: str) -> float:
    """Bonus for exact phrase matches in content."""
    if not query_phrases:
        return 0.0
    content_lower = content.lower()
    hits = sum(1 for p in query_phrases if p in content_lower)
    return min(hits / max(len(query_phrases), 1), 1.0)


def _compute_metadata_score(
    query_tokens: list[str],
    description: str | None,
    filename: str,
) -> tuple[float, list[str]]:
    """Score metadata fields (description + filename) and return reasons."""
    reasons: list[str] = []
    score = 0.0

    # Description match
    if description:
        desc_lower = description.lower()
        desc_hits = sum(1 for t in query_tokens if t in desc_lower)
        if desc_hits > 0:
            desc_score = (desc_hits / len(query_tokens)) * DESCRIPTION_BOOST
            score += desc_score
            matched_terms = [t for t in query_tokens if t in desc_lower]
            reasons.append(f"description:{','.join(matched_terms[:3])}")

    # Filename match
    fname_lower = filename.lower()
    fname_hits = sum(1 for t in query_tokens if t in fname_lower)
    if fname_hits > 0:
        fname_score = (fname_hits / len(query_tokens)) * FILENAME_BOOST
        score += fname_score
        matched_terms = [t for t in query_tokens if t in fname_lower]
        reasons.append(f"filename:{','.join(matched_terms[:3])}")

    return score, reasons


def _compute_recency_boost(mtime_ms: float) -> float:
    """Exponential decay: score 1.0 for today, 0.5 at half-life, → 0.0."""
    days = memory_age_days(mtime_ms)
    if days <= 0:
        return 1.0
    decay = math.exp(-math.log(2) * days / RECENCY_HALF_LIFE_DAYS)
    return max(decay, 0.05)  # floor at 5 %


def _compute_tool_boost(
    query_tokens: list[str],
    content: str,
    recent_tools: tuple[str, ...],
) -> tuple[float, list[str]]:
    """Boost memories that mention recently-used tools (especially warnings)."""
    if not recent_tools:
        return 0.0, []
    content_lower = content.lower()
    reasons: list[str] = []
    boost = 0.0
    for tool in recent_tools:
        tool_lower = tool.lower()
        if tool_lower in content_lower:
            # Check for warning / gotcha keywords near the tool mention
            idx = content_lower.find(tool_lower)
            window = content_lower[max(0, idx - 120) : idx + len(tool_lower) + 120]
            warning_keywords = (
                "warning",
                "gotcha",
                "issue",
                "bug",
                "error",
                "fail",
                "broken",
                "deprecated",
                "avoid",
                "careful",
                "known",
                "limitation",
            )
            is_warning = any(kw in window for kw in warning_keywords)
            contribution = 0.08 if is_warning else 0.03
            boost += contribution
            reasons.append(f"tool:{tool}" + ("(warning)" if is_warning else ""))
    return min(boost, TOOL_BOOST_WEIGHT), reasons


def _compute_type_boost(memory_type: str | None) -> float:
    """Small boost based on memory type (project > feedback > user > reference)."""
    return TYPE_BOOST_MAP.get(memory_type, 0.0)


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------


def score_memory_relevance(
    query: str,
    header: MemoryHeader,
    body_content: str | None = None,
    recent_tools: tuple[str, ...] = (),
) -> tuple[float, list[str]]:
    """Score a single memory file against a query.

    Parameters
    ----------
    query:
        The user query (or search terms).
    header:
        Memory metadata from ``scan_memory_files()``.
    body_content:
        Pre-read body content.  If ``None`` the file is read from disk.
    recent_tools:
        Recently-used tool names for tool-based boosting.

    Returns
    -------
    (score, reasons):
        *score* is 0.0–1.0.  *reasons* are short human-readable tags.
    """
    if not query.strip():
        return 0.0, []

    tokens = _tokenize(query)
    phrases = _extract_phrases(query)

    if body_content is None:
        body_content = _read_memory_body(header.file_path)

    # 1. Content score (primary signal)
    content_score = _compute_content_score(tokens, body_content)
    phrase_bonus = _compute_phrase_score(phrases, body_content)

    # Fuzzy match bonus: kicks in when exact token matching is low
    fuzzy_bonus = 0.0
    if content_score < 0.3 and tokens:
        fuzzy_bonus = _fuzzy_match_score(tokens, body_content)

    doc_score = 0.55 * content_score + 0.30 * phrase_bonus + 0.15 * fuzzy_bonus

    # 2. Metadata score
    meta_score, meta_reasons = _compute_metadata_score(
        tokens, header.description, header.filename
    )

    # 3. Recency boost (applied multiplicatively to content+metadata)
    recency = _compute_recency_boost(header.mtime_ms)

    # 4. Tool boost (additive)
    tool_score, tool_reasons = _compute_tool_boost(tokens, body_content, recent_tools)

    # 5. Type boost (additive, small)
    type_score = _compute_type_boost(header.type)

    # Combine: content dominates, recency gates, metadata+tool+type are additive
    combined = (doc_score * 0.70 + meta_score) * recency + tool_score + type_score

    # Clamp
    final = max(0.0, min(combined, 1.0))

    # Collect reasons (only when score > 0)
    reasons: list[str] = []
    if final > 0:
        reasons.extend(meta_reasons)
        reasons.extend(tool_reasons)
        if content_score > 0.1:
            top_terms = [t for t in tokens if t in body_content.lower()][:3]
            if top_terms:
                reasons.append(f"content:{','.join(top_terms)}")
        if fuzzy_bonus > 0 and content_score <= 0.1:
            reasons.append(f"fuzzy_match:{round(fuzzy_bonus, 3)}")
        if phrase_bonus > 0:
            matched_phrases = [p for p in phrases if p in body_content.lower()][:2]
            if matched_phrases:
                reasons.append(f"phrase:{','.join(matched_phrases)}")
        if header.type:
            reasons.append(f"type:{header.type}")

    return final, reasons


# ---------------------------------------------------------------------------
# LLM-based selection (mirrors TS findRelevantMemories.ts)
# ---------------------------------------------------------------------------

SELECT_MEMORIES_SYSTEM_PROMPT = (
    "You are selecting memories that will be useful to Claude Code as it "
    "processes a user's query. You will be given the user's query and a list "
    "of available memory files with their filenames and descriptions.\n\n"
    "Return a list of filenames for the memories that will clearly be useful "
    "to Claude Code as it processes the user's query (up to 5). Only include "
    "memories that you are certain will be helpful based on their name and "
    "description.\n"
    "- If you are unsure if a memory will be useful in processing the user's "
    "query, then do not include it in your list. Be selective and discerning.\n"
    "- If there are no memories in the list that would clearly be useful, feel "
    "free to return an empty list.\n"
    "- If a list of recently-used tools is provided, do not select memories "
    "that are usage reference or API documentation for those tools (Claude "
    "Code is already exercising them). DO still select memories containing "
    "warnings, gotchas, or known issues about those tools — active use is "
    "exactly when those matter."
)


def _json_parse_safe(text: str) -> dict[str, Any]:
    """Safely parse JSON, returning an empty dict on failure.

    Mirrors TS ``jsonParse`` from utils/slowOperations.
    """
    import json

    if not text or not text.strip():
        return {}
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            return {}
        return result
    except (json.JSONDecodeError, ValueError):
        log_for_debugging(
            f"[memdir] json_parse_safe failed on text: {text[:200]}",
            level="warn",
        )
        return {}


async def _select_via_llm(
    query: str,
    memories: list[MemoryHeader],
    recent_tools: tuple[str, ...],
    signal: object | None = None,
) -> list[str]:
    """Ask a Sonnet side-query to select the most relevant memory filenames.

    Mirrors ``selectRelevantMemories()`` in the TS implementation.
    Returns an empty list when the side-query path is unavailable or errors.

    Logs diagnostic information on failure (matching TS ``logForDebugging``
    behaviour) unless the signal has been aborted.
    """
    valid_filenames = {m.filename for m in memories}
    manifest = format_memory_manifest(memories)

    tools_section = ""
    if recent_tools:
        tools_section = f"\n\nRecently used tools: {', '.join(recent_tools)}"

    prompt = f"Query: {query}\n\nAvailable memories:\n{manifest}{tools_section}"

    def _signal_aborted() -> bool:
        """Check if the signal has been aborted."""
        if signal is None:
            return False
        try:
            if getattr(signal, "aborted", False):
                return True
            is_set = getattr(signal, "is_set", None)
            if callable(is_set) and is_set():
                return True
        except Exception:
            pass
        return False

    try:
        from hare.utils.side_query import side_query  # type: ignore[import-untyped]

        result = await side_query(
            model=None,  # will use default Sonnet
            system=SELECT_MEMORIES_SYSTEM_PROMPT,
            skip_system_prompt_prefix=True,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            output_format={
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "selected_memories": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["selected_memories"],
                    "additionalProperties": False,
                },
            },
            signal=signal,
            query_source="memdir_relevance",
        )

        text_block = None
        for block in result.get("content", []):
            if block.get("type") == "text":
                text_block = block
                break

        if text_block is None:
            log_for_debugging(
                "[memdir] selectRelevantMemories: no text block in response",
                level="warn",
            )
            return []

        parsed = _json_parse_safe(text_block.get("text", ""))
        selected: list[str] = parsed.get("selected_memories", [])
        if not isinstance(selected, list):
            selected = []
        return [f for f in selected if isinstance(f, str) and f in valid_filenames]

    except ImportError:
        # side_query module not available — fall back to local scoring
        return []
    except Exception as e:
        # Mirror TS: only log when not aborted
        if not _signal_aborted():
            log_for_debugging(
                f"[memdir] selectRelevantMemories failed: {error_message(e)}",
                level="warn",
            )
        return []


# ---------------------------------------------------------------------------
# Memory shape telemetry (mirrors TS memoryShapeTelemetry.ts)
# ---------------------------------------------------------------------------


def _log_memory_selection_shape(
    candidates: list[MemoryHeader],
    selected: list[RelevantMemory],
) -> None:
    """Log memory recall shape for telemetry (fires on every invocation).

    Mirrors TS ``logMemoryRecallShape``.  The telemetry system uses this to
    track selection rates, type distributions, and recall density over time.
    Fires even when *selected* is empty so the denominator is always known.
    """
    import os

    # Only emit telemetry when CLAUDE_CODE_MEMORY_TELEMETRY is enabled.
    if os.environ.get("CLAUDE_CODE_MEMORY_TELEMETRY") != "1":
        return

    try:
        selected_paths = {s.path for s in selected}

        # Count types among candidates and selected
        type_counts: dict[str | None, int] = {}
        selected_type_counts: dict[str | None, int] = {}
        for m in candidates:
            t = m.type
            type_counts[t] = type_counts.get(t, 0) + 1
            if m.file_path in selected_paths:
                selected_type_counts[t] = selected_type_counts.get(t, 0) + 1

        # Age distribution of selected memories (in days)
        from hare.memdir.memory_age import memory_age_days

        selected_ages = [memory_age_days(s.mtime_ms) for s in selected]

        payload = {
            "total_candidates": len(candidates),
            "total_selected": len(selected),
            "selection_rate": (
                round(len(selected) / max(len(candidates), 1), 4)
            ),
            "type_distribution": {
                str(k): v for k, v in type_counts.items()
            },
            "selected_type_distribution": {
                str(k): v for k, v in selected_type_counts.items()
            },
            "selected_ages_days": selected_ages,
            "min_age_days": min(selected_ages) if selected_ages else -1,
            "max_age_days": max(selected_ages) if selected_ages else -1,
            "avg_age_days": (
                round(sum(selected_ages) / len(selected_ages), 1)
                if selected_ages
                else -1
            ),
        }

        import json

        log_for_debugging(
            f"[memdir] memory_shape_telemetry: {json.dumps(payload)}",
            level="telemetry",
        )
    except Exception as e:
        # Telemetry must never throw
        log_for_debugging(
            f"[memdir] memory_shape_telemetry error: {error_message(e)}",
            level="error",
        )


# ---------------------------------------------------------------------------
# Local scoring selection (fallback when LLM path unavailable)
# ---------------------------------------------------------------------------


async def _select_via_local_scoring(
    query: str,
    memories: list[MemoryHeader],
    recent_tools: tuple[str, ...],
    signal: object | None = None,
) -> list[tuple[MemoryHeader, float, list[str]]]:
    """Score every memory locally, return top *MAX_SELECTED_MEMORIES* above threshold.

    Uses concurrent I/O for reading memory bodies (via ``asyncio.to_thread``
    on Python ≥ 3.9) when there are many files; falls back to sequential
    reads for small directories to avoid thread-pool overhead.
    """
    if not query.strip():
        return []

    tokens = _tokenize(query)

    def _check_signal() -> bool:
        """Return True if the signal is set/aborted."""
        if signal is None:
            return False
        try:
            if getattr(signal, "aborted", False):
                return True
            is_set = getattr(signal, "is_set", None)
            if callable(is_set) and is_set():
                return True
        except Exception:
            pass
        return False

    # Concurrent body reads for larger directories (> 20 files).
    # For small directories sequential is simpler and avoids thread overhead.
    if len(memories) > 20 and hasattr(asyncio, "to_thread"):

        async def _read_and_score(m: MemoryHeader) -> tuple[MemoryHeader, float, list[str]]:
            if _check_signal():
                return (m, 0.0, [])
            body = await asyncio.to_thread(_read_memory_body, m.file_path)
            score, reasons = score_memory_relevance(
                query, m, body_content=body, recent_tools=recent_tools
            )
            return (m, score, reasons)

        tasks = [_read_and_score(m) for m in memories]
        scored_raw = await asyncio.gather(*tasks, return_exceptions=True)
        scored: list[tuple[MemoryHeader, float, list[str]]] = []
        for item in scored_raw:
            if isinstance(item, BaseException):
                log_for_debugging(
                    f"[memdir] local_scoring read failed: {error_message(item)}",
                    level="warn",
                )
                continue
            scored.append(item)
    else:
        scored = []
        for m in memories:
            if _check_signal():
                break
            body = _read_memory_body(m.file_path)
            score, reasons = score_memory_relevance(
                query, m, body_content=body, recent_tools=recent_tools
            )
            scored.append((m, score, reasons))

    # Sort descending by score
    scored.sort(key=lambda x: x[1], reverse=True)

    # Deduplicate by file path (keep highest score)
    seen_paths: set[str] = set()
    deduped: list[tuple[MemoryHeader, float, list[str]]] = []
    for item in scored:
        path = item[0].file_path
        if path not in seen_paths:
            seen_paths.add(path)
            deduped.append(item)

    # Only return items with a minimum content signal when query has substance
    if tokens:
        deduped = [
            s
            for s in deduped
            if s[1] >= MIN_CONTENT_SCORE_THRESHOLD
            or any(
                r.startswith("description:") or r.startswith("filename:")
                for r in s[2]
            )
        ]

    return deduped[:MAX_SELECTED_MEMORIES]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def find_relevant_memories(
    query: str,
    memory_dir: str,
    signal: object | None = None,
    recent_tools: tuple[str, ...] = (),
    already_surfaced: frozenset[str] | set[str] = frozenset(),
) -> list[RelevantMemory]:
    """Find memory files relevant to *query*.

    Strategy (mirrors TS ``findRelevantMemories``):
    1. Scan the memory directory.
    2. Filter out already-surfaced files.
    3. Try LLM-based selection via side-query (if available).
    4. Fall back to local relevance scoring.
    5. Log memory shape telemetry (when enabled).

    Parameters
    ----------
    query:
        The user query to match against.
    memory_dir:
        Absolute path to the memory storage directory.
    signal:
        Optional abort signal (asyncio.Event or AbortSignal-like).
    recent_tools:
        Names of tools recently used in the session.
    already_surfaced:
        Set of absolute paths already shown to the model in prior turns.

    Returns
    -------
    list[RelevantMemory]:
        Up to 5 most relevant memories, sorted by relevance (best first).
    """
    # 1. Scan
    try:
        all_memories = await scan_memory_files(memory_dir, signal)
    except Exception:
        return []

    # 2. Filter already-surfaced
    memories = [m for m in all_memories if m.file_path not in already_surfaced]
    if not memories:
        return []

    # 3. Try LLM selection
    llm_selected = await _select_via_llm(query, memories, recent_tools, signal)
    if llm_selected:
        by_filename = {m.filename: m for m in memories}
        result: list[RelevantMemory] = []
        for fname in llm_selected:
            m = by_filename.get(fname)
            if m is not None:
                result.append(
                    RelevantMemory(
                        path=m.file_path,
                        mtime_ms=m.mtime_ms,
                        score=1.0,  # LLM-selected → assumed highly relevant
                        match_reasons=["llm_selected"],
                    )
                )
        _log_memory_selection_shape(memories, result)
        return result

    # 4. Local scoring fallback
    scored = await _select_via_local_scoring(
        query, memories, recent_tools, signal
    )
    selected = [
        RelevantMemory(
            path=m.file_path,
            mtime_ms=m.mtime_ms,
            score=round(score, 4),
            match_reasons=reasons,
        )
        for m, score, reasons in scored
    ]

    # 5. Log telemetry even on local-scored results
    _log_memory_selection_shape(memories, selected)
    return selected


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def search_memories(
    query: str,
    memory_dir: str,
    *,
    max_results: int = 20,
    min_score: float = 0.01,
    memory_types: tuple[str, ...] | None = None,
    signal: object | None = None,
) -> list[SearchResult]:
    """Full-text search across memory files.

    Reads memory bodies, scores each file against *query*, and returns results
    ordered by relevance with short content snippets.

    Parameters
    ----------
    query:
        Search query (free-text).
    memory_dir:
        Absolute path to the memory storage directory.
    max_results:
        Maximum number of results to return (default 20).
    min_score:
        Minimum relevance score to include a result (default 0.01).
    memory_types:
        Optional filter: only include memories of these types
        (``"user"``, ``"feedback"``, ``"project"``, ``"reference"``).
    signal:
        Optional abort signal.

    Returns
    -------
    list[SearchResult]:
        Results ordered by relevance score (best first).
    """
    try:
        memories = await scan_memory_files(memory_dir, signal)
    except Exception:
        return []

    # Filter by type if requested
    if memory_types:
        type_set = set(memory_types)
        memories = [m for m in memories if m.type in type_set]

    if not memories or not query.strip():
        return []

    tokens = _tokenize(query)
    query_lower = query.lower()
    results: list[SearchResult] = []

    for m in memories:
        if signal is not None:
            try:
                if getattr(signal, "aborted", False) or getattr(
                    signal, "is_set", lambda: False
                )():
                    break
            except Exception:
                pass

        body = _read_memory_body(m.file_path)
        if not body:
            continue

        score, reasons = score_memory_relevance(
            query, m, body_content=body, recent_tools=()
        )
        if score < min_score:
            continue

        # Extract a snippet: find the first line containing a query token.
        snippet = _extract_snippet(body, tokens, query_lower)

        results.append(
            SearchResult(
                path=m.file_path,
                mtime_ms=m.mtime_ms,
                score=round(score, 4),
                match_reasons=reasons,
                snippet=snippet,
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:max_results]


def _extract_snippet(
    body: str, tokens: list[str], query_lower: str
) -> str:
    """Extract the first line (or up to 200 chars) containing a query match.

    If no token match is found, returns the first non-blank line of the body.
    Returns ``""`` if the body is empty or contains only whitespace.

    Edge cases:
    - Empty body → ``""``
    - No matching line → first non-blank line
    - Matching line is very long → truncated with ellipsis
    - Unicode / emoji content → handled safely via Python str slicing
    """
    max_snippet_len = 200
    lines = body.splitlines()

    # Try to find a line with a query token match
    for line in lines:
        line_lower = line.lower()
        if not line.strip():
            continue
        if any(t in line_lower for t in tokens):
            stripped = line.strip()
            if len(stripped) > max_snippet_len:
                stripped = stripped[: max_snippet_len - 3] + "..."
            return stripped

    # Fallback: return first non-blank line
    for line in lines:
        stripped = line.strip()
        if stripped:
            if len(stripped) > max_snippet_len:
                stripped = stripped[: max_snippet_len - 3] + "..."
            return stripped

    return ""


# ---------------------------------------------------------------------------
# Convenience: single-memory scoring (used by tests / REPL)
# ---------------------------------------------------------------------------


def score_single_memory(
    query: str,
    file_path: str,
    recent_tools: tuple[str, ...] = (),
) -> tuple[float, list[str]]:
    """Score a single memory file by reading its header and body.

    Synchronous convenience wrapper — useful for debugging and testing.
    """
    from hare.memdir.memory_scan import _parse_frontmatter_simple

    try:
        st = os.stat(file_path)
        with open(file_path, encoding="utf-8") as fh:
            raw = fh.read(MAX_CONTENT_READ_BYTES + 512)
    except OSError:
        return 0.0, []

    # Parse frontmatter
    fm = _parse_frontmatter_simple(raw)
    desc = fm.get("description")
    mem_type_raw = fm.get("type")

    from hare.memdir.memory_types import parse_memory_type

    mem_type = parse_memory_type(mem_type_raw)

    # Build a synthetic header
    header = MemoryHeader(
        filename=os.path.basename(file_path),
        file_path=file_path,
        mtime_ms=st.st_mtime * 1000,
        description=desc,
        type=mem_type,
    )

    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        body = parts[2] if len(parts) >= 3 else raw
    body = body[:MAX_CONTENT_READ_BYTES]

    return score_memory_relevance(query, header, body_content=body, recent_tools=recent_tools)


# ---------------------------------------------------------------------------
# Batch scoring (for bulk operations / tool calls)
# ---------------------------------------------------------------------------


def batch_score_memories(
    query: str,
    memory_dir: str,
    *,
    max_results: int = MAX_SELECTED_MEMORIES,
    min_score: float = MIN_CONTENT_SCORE_THRESHOLD,
    memory_types: tuple[str, ...] | None = None,
    recent_tools: tuple[str, ...] = (),
) -> list[RelevantMemory]:
    """Score all memories in a directory synchronously.

    This is a synchronous convenience wrapper useful for:
    - REPL/debugging tools that call into the memory system
    - One-shot scoring when no async loop is available
    - Testing and benchmarking

    Parameters
    ----------
    query:
        The search query.
    memory_dir:
        Absolute path to the memory storage directory.
    max_results:
        Maximum number of results to return.
    min_score:
        Minimum relevance score to include a result.
    memory_types:
        Optional filter: only include memories of these types.
    recent_tools:
        Names of tools recently used for tool-based boosting.

    Returns
    -------
    list[RelevantMemory]:
        Scored and sorted memories, best first.
    """
    import asyncio

    async def _batch() -> list[RelevantMemory]:
        try:
            all_memories = await scan_memory_files(memory_dir, None)
        except Exception:
            return []

        if memory_types:
            type_set = set(memory_types)
            all_memories = [m for m in all_memories if m.type in type_set]

        if not all_memories or not query.strip():
            return []

        scored: list[tuple[MemoryHeader, float, list[str]]] = []
        for m in all_memories:
            body = _read_memory_body(m.file_path)
            score, reasons = score_memory_relevance(
                query, m, body_content=body, recent_tools=recent_tools
            )
            scored.append((m, score, reasons))

        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            RelevantMemory(
                path=m.file_path,
                mtime_ms=m.mtime_ms,
                score=round(s, 4),
                match_reasons=r,
            )
            for m, s, r in scored
            if s >= min_score
        ][:max_results]

    try:
        return asyncio.run(_batch())
    except RuntimeError:
        # Already inside an event loop — create a new one
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_batch())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Convenience: path-only retrieval
# ---------------------------------------------------------------------------


def find_relevant_memory_paths(
    query: str,
    memory_dir: str,
    *,
    max_results: int = MAX_SELECTED_MEMORIES,
    min_score: float = MIN_CONTENT_SCORE_THRESHOLD,
    recent_tools: tuple[str, ...] = (),
    already_surfaced: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    """Convenience wrapper: return only absolute paths of relevant memories.

    Synchronous wrapper around ``find_relevant_memories``.  Useful when
    callers only need the file paths (e.g., to inject into prompts).

    Parameters
    ----------
    query:
        The user query to match against.
    memory_dir:
        Absolute path to the memory storage directory.
    max_results:
        Maximum number of results to return.
    min_score:
        Minimum relevance score.
    recent_tools:
        Names of tools recently used in the session.
    already_surfaced:
        Set of absolute paths already surfaced to the model.

    Returns
    -------
    list[str]:
        Absolute paths of relevant memory files, best first.
    """
    import asyncio

    async def _find() -> list[str]:
        results = await find_relevant_memories(
            query=query,
            memory_dir=memory_dir,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
        )
        return [r.path for r in results if r.score >= min_score][:max_results]

    try:
        return asyncio.run(_find())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_find())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Fuzzy / typo-tolerant scoring helpers
# ---------------------------------------------------------------------------


def _levenshtein_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings.

    Optimised with O(min(|a|,|b|)) space. Used for typo-tolerant matching
    when exact token matches fail.
    """
    if len(a) < len(b):
        return _levenshtein_distance(b, a)

    if len(b) == 0:
        return len(a)

    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr_row = [i]
        for j, cb in enumerate(b, 1):
            insertions = prev_row[j] + 1
            deletions = curr_row[j - 1] + 1
            substitutions = prev_row[j - 1] + (ca != cb)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]


def _fuzzy_match_score(query_tokens: list[str], content: str) -> float:
    """Bonus score for fuzzy (typo-tolerant) token matches in content.

    Applied when exact token matches score below threshold — catches
    minor typos, hyphenation variations, and plural/singular forms.
    Returns a value in [0.0, 1.0].
    """
    if not query_tokens or not content:
        return 0.0

    content_lower = content.lower()
    fuzzy_hits = 0
    max_distance = 2  # Allow up to 2 edits for tokens ≥ 4 chars

    for token in query_tokens:
        if len(token) < 4:
            continue  # skip short tokens for fuzzy matching (noisy)
        if token in content_lower:
            fuzzy_hits += 1
            continue
        # Check if a substring within 1.5× length has a close edit distance
        window = max(len(token) + max_distance, int(len(token) * 1.5))
        for i in range(len(content_lower) - len(token) + 1):
            substr = content_lower[i : i + window]
            dist = _levenshtein_distance(token, substr[: len(token)])
            if dist <= max_distance:
                fuzzy_hits += 0.5  # partial credit for fuzzy match
                break

    return min(fuzzy_hits / max(len(query_tokens), 1), 1.0) * 0.3  # capped at 0.3
