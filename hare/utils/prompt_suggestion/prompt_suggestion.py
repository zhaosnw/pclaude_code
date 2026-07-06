"""Prompt suggestion utils: normalization, quality scoring, validation,
deduplication, and display formatting."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------------------
# Types

@dataclass
class PromptSuggestion:
    text: str
    prompt_id: str = "user_intent"
    generation_request_id: str | None = None

@dataclass
class SuggestionCandidate:
    text: str
    priority: int = 0
    score: float = 0.0
    tags: frozenset[str] = field(default_factory=frozenset)
    source: str = "model"

# ---------------------------------------------------------------------------
# Constants

MAX_SUGGESTION_LENGTH = 100
MAX_SUGGESTION_WORDS = 12
MIN_SUGGESTION_WORDS = 2

_ALLOWED_SINGLE_WORDS: frozenset[str] = frozenset({
    "yes", "yeah", "yep", "yea", "yup", "sure", "ok", "okay",
    "push", "commit", "deploy", "stop", "continue", "check",
    "exit", "quit", "no",
})

_EVALUATIVE_RE = re.compile(
    r"\b(thanks|thank you|looks good|sounds good|that works|"
    r"that worked|that's all|nice|great|perfect|makes sense|"
    r"awesome|excellent)\b", re.IGNORECASE)

_CLAUDE_VOICE_RE = re.compile(
    r"^(let me|i['’]ll|i['’]ve|i['’]m|i can|i would|i think|"
    r"i notice|here['’]s|here is|here are|that['’]s|this is|"
    r"this will|you can|you should|you could|sure,|of course|certainly)",
    re.IGNORECASE)

_MULTI_SENTENCE_RE = re.compile(r"[.!?]\s+[A-Z]")
_FORMATTING_RE = re.compile(r"[\n*]|\*\*")

# ---------------------------------------------------------------------------
# Normalization

def normalize_suggestion_text(raw: str) -> str:
    """Strip quotes, whitespace, and trailing punctuation from model output.
    Preserves leading slash for slash-commands (e.g. /compact)."""
    text = raw.strip()
    if len(text) >= 2:
        if text[0] == text[-1] and text[0] in ('"', "'", "“", "‘"):
            text = text[1:-1].strip()
        elif text[0] == "“" and text[-1] == "”":
            text = text[1:-1].strip()
    text = re.sub(r"\s{2,}", " ", text)
    if not text.startswith("/"):
        text = text.strip(".,;:- \t")
    # Strip trailing sentence period unless it's an abbreviation like "etc."
    if text.endswith(".") and not re.search(r"\b\w{1,2}\.$", text):
        text = text[:-1].rstrip()
    return text

def build_suggestion_placeholder(
    suggestion: str, *, dim: bool = True, prefix: str = "", suffix: str = ""
) -> str:
    """Render terminal-formatted suggestion; optionally dimmed via ANSI escapes.
    Strips any existing ANSI escapes and zero-width characters first."""
    sanitized = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", suggestion)
    for c in ("​", "‌", "‍", "﻿"):
        sanitized = sanitized.replace(c, "")
    sanitized = re.sub(r"[  -   　]", " ", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized).strip()
    if dim:
        sanitized = f"\x1b[2m{sanitized}\x1b[0m"
    return f"{prefix}{sanitized}{suffix}"

# ---------------------------------------------------------------------------
# Quality scoring

def score_suggestion_quality(suggestion: str) -> float:
    """Score 0.0-1.0. Penalizes meta-text, evaluative language, Claude-voice,
    multi-sentence output, formatting, and length violations. Hard-vetoes known
    meta outputs ('done', 'nothing found', wrapped meta-reasoning). Boosts
    slash commands (>=0.85) and mid-length specificity."""
    if not suggestion or not suggestion.strip():
        return 0.0
    lower = suggestion.lower().strip()
    word_count = len(suggestion.strip().split())
    # Hard vetoes
    if lower in ("done", "nothing found", "nothing found."):
        return 0.0
    if re.match(r"^[(\[].*[)\]]$", suggestion):
        return 0.0
    if lower.startswith("nothing to") or lower.startswith("no suggestion"):
        return 0.0
    if re.search(r"\bstay(s|ing)? silent\b|\bsilence is\b", lower):
        return 0.0
    score = 1.0
    # Length penalties (exempt slash commands and common single-word inputs)
    if word_count < MIN_SUGGESTION_WORDS:
        if not (suggestion.startswith("/") or lower in _ALLOWED_SINGLE_WORDS):
            score -= 0.5
    if word_count > MAX_SUGGESTION_WORDS:
        score -= 0.3
    if len(suggestion) >= MAX_SUGGESTION_LENGTH:
        score -= 0.3
    # Content quality penalties
    if _EVALUATIVE_RE.search(lower):
        score -= 0.4
    if _CLAUDE_VOICE_RE.search(suggestion):
        score -= 0.4
    if _MULTI_SENTENCE_RE.search(suggestion):
        score -= 0.3
    if _FORMATTING_RE.search(suggestion):
        score -= 0.2
    # Specificity boost
    if MIN_SUGGESTION_WORDS <= word_count <= 6 and len(suggestion) >= 10:
        score += 0.1
    if suggestion.startswith("/"):
        score = max(score, 0.85)
    return max(0.0, min(1.0, score))

def rank_candidates(
    candidates: Iterable[SuggestionCandidate],
) -> list[SuggestionCandidate]:
    """Sort by descending score then priority; deduplicate case-insensitively.
    Drops candidates with score <= 0.0."""
    scored = sorted(
        (c for c in candidates if c.score > 0.0),
        key=lambda c: (-c.score, -c.priority),
    )
    seen: set[str] = set()
    result: list[SuggestionCandidate] = []
    for c in scored:
        key = c.text.strip().lower()
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result

# ---------------------------------------------------------------------------
# Validation and deduplication

def is_valid_suggestion(text: str | None) -> bool:
    """Lightweight guard: non-empty, not meta-text, within word/length bounds.
    Allows slash commands and common single-word inputs (yes, push, etc.)."""
    if not text or not text.strip():
        return False
    lower = text.strip().lower()
    word_count = len(text.strip().split())
    if lower in ("done", "nothing", "nothing found", "nothing found."):
        return False
    if lower.startswith("nothing to") or re.search(
        r"stay(s|ing)? silent|silence is", lower
    ):
        return False
    if word_count < MIN_SUGGESTION_WORDS:
        if not (text.strip().startswith("/") or lower in _ALLOWED_SINGLE_WORDS):
            return False
    if word_count > MAX_SUGGESTION_WORDS or len(text) >= MAX_SUGGESTION_LENGTH:
        return False
    return True

def deduplicate_suggestions(
    suggestions: list[PromptSuggestion],
) -> list[PromptSuggestion]:
    """Deduplicate by case-insensitive text; preserves first-occurrence order."""
    seen: set[str] = set()
    result: list[PromptSuggestion] = []
    for s in suggestions:
        key = s.text.strip().lower()
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


# ---------------------------------------------------------------------------
# Suggestion classification
# ---------------------------------------------------------------------------

_SLASH_COMMAND_RE = re.compile(r"^/\w+")

_CONTINUATION_MARKERS: frozenset[str] = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
    "go ahead", "continue", "proceed", "do it", "go on",
})

_ACTION_VERB_RE = re.compile(
    r"^(run|add|fix|create|build|test|deploy|commit|push|pull|"
    r"merge|rebase|checkout|switch|update|remove|delete|rename|"
    r"move|copy|refactor|install|upgrade|downgrade|rollback|"
    r"lint|format|optimize|debug|search|find|show|open|start|"
    r"stop|restart|edit|write|read|explain|analyze|generate|"
    r"convert|compile|publish)\b",
    re.IGNORECASE,
)


def classify_suggestion(text: str) -> str:
    """Classify a suggestion into a category: 'slash_command', 'continuation',
    'action', or 'open_ended'. Used by UI rendering and analytics.

    >>> classify_suggestion('/compact')
    'slash_command'
    >>> classify_suggestion('yes')
    'continuation'
    >>> classify_suggestion('run the tests')
    'action'
    >>> classify_suggestion('then what about the performance?')
    'open_ended'
    """
    stripped = text.strip()
    lower = stripped.lower()

    if _SLASH_COMMAND_RE.match(stripped):
        return "slash_command"

    if lower in _CONTINUATION_MARKERS or lower.startswith(("yes ", "sure ")):
        return "continuation"

    if _ACTION_VERB_RE.match(stripped):
        return "action"

    return "open_ended"


# ---------------------------------------------------------------------------
# Raw suggestion extraction
# ---------------------------------------------------------------------------

_EXCESS_QUOTING_RE = re.compile(r"^[\"'](.+)[\"']$", re.DOTALL)
_LINE_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]\s*|[-*+]\s+)(.+?)\s*$")
_LEADING_LABEL_RE = re.compile(r"^(?:next|command|suggestion|reply|user)[:)]\s*", re.IGNORECASE)
_TRAILING_EXPLANATION_RE = re.compile(r"\s*--.*$|\s*\(because.*\)$|\s*\(since\s.*\)$", re.IGNORECASE)
_CLEANUP_WHITESPACE_RE = re.compile(r"\s{2,}")

_MAX_EXTRACTED_LINES = 6


def _iter_raw_lines(text: str) -> list[str]:
    """Split raw model output into candidate lines for suggestion extraction."""
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        # Skip lines that are clearly meta-instruction echoes
        lower = stripped.lower()
        if lower.startswith(("suggestion mode", "reply with", "format:", "never")):
            continue
        lines.append(stripped)
    return lines


def extract_suggestions_from_text(raw: str) -> list[str]:
    """Parse raw model output into individual suggestion strings.

    Handles common model artifacts: wrapping quotes, bullet lists, numbered
    lists, leading labels ('suggestion:', 'command:'), and trailing
    explanations. Returns up to _MAX_EXTRACTED_LINES cleaned candidates.

    >>> extract_suggestions_from_text('"run the tests"')
    ['run the tests']
    >>> extract_suggestions_from_text('1. run the tests\\n2. commit this')
    ['run the tests', 'commit this']
    """
    if not raw or not raw.strip():
        return []

    lines = _iter_raw_lines(raw)
    if len(lines) > _MAX_EXTRACTED_LINES:
        lines = lines[:_MAX_EXTRACTED_LINES]

    candidates: list[str] = []
    for line in lines:
        # Strip wrapping quotes surrounding the entire line
        m = _EXCESS_QUOTING_RE.match(line)
        if m:
            line = m.group(1).strip()

        # Strip list-item markers (1., 2), - , *, +)
        m = _LINE_ITEM_RE.match(line)
        if m:
            line = m.group(1).strip()

        # Strip leading labels
        line = _LEADING_LABEL_RE.sub("", line).strip()

        # Strip trailing explanations
        line = _TRAILING_EXPLANATION_RE.sub("", line).strip()

        # Collapse internal whitespace
        line = _CLEANUP_WHITESPACE_RE.sub(" ", line)

        if line:
            candidates.append(line)

    # Fallback: treat the whole raw text as one candidate
    if not candidates:
        single = _EXCESS_QUOTING_RE.sub(r"\1", raw.strip())
        single = _LEADING_LABEL_RE.sub("", single).strip()
        if single:
            candidates.append(single)

    return candidates


# ---------------------------------------------------------------------------
# Suggestion display helpers
# ---------------------------------------------------------------------------

_ELISION_MARKER = "…"

_SUGGESTION_DISPLAY_MAX = 60


def truncate_suggestion(text: str, max_len: int = _SUGGESTION_DISPLAY_MAX) -> str:
    """Truncate a suggestion for display, preserving word boundaries.

    Always returns at least one complete word. Adds an elision marker (…) when
    truncated.

    >>> truncate_suggestion('run the tests and commit the changes', max_len=15)
    'run the tests…'
    """
    if len(text) <= max_len:
        return text

    cut_pos = max_len - len(_ELISION_MARKER)
    candidate = text[:cut_pos]

    # Only walk back to a word boundary if we cut mid-word (the character at
    # the cut point is not whitespace, meaning we are splitting a word).
    if cut_pos < len(text) and candidate and not text[cut_pos].isspace():
        last_space = candidate.rfind(" ")
        if last_space > 0:
            candidate = candidate[:last_space]

    candidate = candidate.rstrip()
    if not candidate:
        return text[:cut_pos] + _ELISION_MARKER

    return candidate + _ELISION_MARKER


# ---------------------------------------------------------------------------
# Session-scoped suggestion history (LRU-bounded deduplication)
# ---------------------------------------------------------------------------

_MAX_HISTORY_SIZE = 50


class SuggestionHistory:
    """Lightweight LRU-bounded history tracker for session-level deduplication.

    Tracks recently emitted suggestions so that near-duplicates and exact
    repeats are not shown back-to-back. Also maintains a count of how many
    times each key has been seen for frequency-based suppression.
    """

    def __init__(self, max_size: int = _MAX_HISTORY_SIZE) -> None:
        self._max_size = max_size
        self._keys: list[str] = []
        self._counts: dict[str, int] = {}
        self._recent_texts: set[str] = set()

    @staticmethod
    def _normalize_key(text: str) -> str:
        """Normalize text into a stable dedup key: lowercase, stripped, single-spaced."""
        return _CLEANUP_WHITESPACE_RE.sub(" ", text.strip().lower())

    def seen(self, text: str) -> bool:
        """Check whether this text (or a normalized variant) has been recorded."""
        key = self._normalize_key(text)
        return key in self._recent_texts

    def record(self, text: str) -> None:
        """Record a suggestion as emitted; evicts oldest entry if at capacity."""
        key = self._normalize_key(text)
        if key in self._recent_texts:
            # Bump to most-recent; increment count
            self._keys.remove(key)
            self._keys.append(key)
            self._counts[key] = self._counts.get(key, 0) + 1
            return

        if len(self._keys) >= self._max_size:
            evicted = self._keys.pop(0)
            self._recent_texts.discard(evicted)
            self._counts.pop(evicted, None)

        self._keys.append(key)
        self._recent_texts.add(key)
        self._counts[key] = 1

    def frequency(self, text: str) -> int:
        """Return how many times this text (by normalized key) has been recorded."""
        return self._counts.get(self._normalize_key(text), 0)

    def clear(self) -> None:
        """Reset history."""
        self._keys.clear()
        self._counts.clear()
        self._recent_texts.clear()

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, text: str) -> bool:
        return self.seen(text)


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------

_DUPLICATE_SCORE_REDUCTION = 0.3


def process_suggestions(
    raw: str,
    *,
    history: SuggestionHistory | None = None,
    min_score: float = 0.3,
    max_results: int = 3,
    source: str = "model",
) -> list[SuggestionCandidate]:
    """Full pipeline: extract → normalize → score → rank → deduplicate (with history).

    This is the primary entry point for turning raw model output into a ranked
    list of display-ready suggestion candidates.

    Args:
        raw: Raw model output text (may include artifacts like quotes, bullets).
        history: Optional SuggestionHistory for cross-invocation deduplication.
        min_score: Minimum quality score (0.0-1.0) for inclusion.
        max_results: Maximum number of candidates to return.
        source: Label attached to each candidate (default "model").

    Returns:
        Ranked list of SuggestionCandidate, highest score first.
    """
    extracted = extract_suggestions_from_text(raw)
    if not extracted:
        return []

    candidates: list[SuggestionCandidate] = []
    for text in extracted:
        normalized = normalize_suggestion_text(text)
        if not normalized or not is_valid_suggestion(normalized):
            continue

        score = score_suggestion_quality(normalized)

        # Penalize suggestions the user has already seen
        if history is not None and history.seen(normalized):
            score = max(0.0, score - _DUPLICATE_SCORE_REDUCTION)
            # Further penalize suggestions seen multiple times
            freq = history.frequency(normalized)
            if freq > 1:
                score = max(0.0, score - 0.1 * min(freq - 1, 5))

        if score < min_score:
            continue

        candidates.append(
            SuggestionCandidate(
                text=normalized,
                score=score,
                source=source,
            )
        )

    ranked = rank_candidates(candidates)
    return ranked[:max_results]


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import doctest
    doctest.testmod()
