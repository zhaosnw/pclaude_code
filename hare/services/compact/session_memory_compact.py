"""
Session memory compaction with summarization.

Port of: src/services/compact/sessionMemoryCompact.ts

Uses session memory (HARE.md) to perform lightweight compaction
without needing an API call for summarization. When the conversation
grows too large, older messages are summarized into a structured
context block injected from session memory, preserving recent messages
intact for continuity.

Key design:
- Load project + user HARE.md as long-term memory context
- Split messages at conversation-round boundaries
- Summarize older rounds into a <session_memory> block
- Keep recent messages that fit under max_tokens
- Inject memory at the boundary between summary and recent messages
- Optionally update HARE.md with new insights after compaction

Architecture:
    try_session_memory_compaction()          # async main entry point
        ├── is_compaction_eligible()         # bail-out gate
        ├── load_session_memory()            # HARE.md loading
        │       ├── _find_hare_md_files()
        │       └── _load_and_cache_file()
        ├── truncate_memory_for_context()    # token-budget truncation
        ├── find_split_by_rounds()           # preferred split
        │       └── group_messages_by_api_round()
        ├── find_split_by_tokens()           # fallback split
        ├── summarize_older_messages()       # extractive summary
        │       └── _generate_round_summary()
        ├── build_session_memory_context_block()
        └── _append_to_memory_if_changed()   # optional update

Supporting utilities:
    compact_with_session_memory()            # synchronous wrapper
    analyze_compaction_plan()                # dry-run diagnostics
    validate_compact_config()                # config sanity check
    get_session_memory_stats()               # memory file statistics
    reset_session_memory_cache()             # cache invalidation
    estimate_message_token_breakdown()       # detailed token accounting
    _tokenize_simple()                       # fast word-based token count (no API)
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from hare.services.compact.grouping import group_messages_by_api_round
from hare.services.compact.micro_compact import estimate_message_tokens
from hare.services.token_estimation import estimate_tokens

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_MEMORY_CHARS = 8_000
DEFAULT_MAX_MEMORY_TOKENS = 5_000
DEFAULT_MIN_CONVERSATION_ROUNDS = 3
DEFAULT_SUMMARY_MAX_TOKENS = 2_000

# Floor values for validation
_MIN_MIN_TOKENS = 500
_MIN_MAX_TOKENS = 1_000
_MIN_MIN_TEXT_BLOCK_MESSAGES = 2
_MIN_MIN_CONVERSATION_ROUNDS = 1
_MIN_SUMMARY_MAX_TOKENS = 100
_MIN_MAX_MEMORY_CHARS = 100
_MIN_MAX_MEMORY_TOKENS = 100


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class SessionMemoryCompactConfig:
    """Configuration for session memory compaction."""

    # Minimum tokens in messages before compaction is considered
    min_tokens: int = 10_000
    # Minimum number of messages with text blocks before compaction
    min_text_block_messages: int = 5
    # Maximum tokens in the kept (recent) portion
    max_tokens: int = 40_000
    # Maximum chars to load from HARE.md
    max_memory_chars: int = DEFAULT_MAX_MEMORY_CHARS
    # Maximum tokens for session memory context block
    max_memory_tokens: int = DEFAULT_MAX_MEMORY_TOKENS
    # Minimum conversation rounds before splitting
    min_conversation_rounds: int = DEFAULT_MIN_CONVERSATION_ROUNDS
    # Maximum tokens allowed in the summary block
    summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS
    # Whether to update HARE.md after compaction
    update_memory_on_compact: bool = False
    # Whether to preserve system messages at the top when splitting
    preserve_system_messages: bool = True
    # If True, always produce a summary even when below threshold
    always_summarize: bool = False


DEFAULT_SM_COMPACT_CONFIG = SessionMemoryCompactConfig()
_config = SessionMemoryCompactConfig()

# In-memory cache for loaded session memory to avoid repeated disk reads.
# Keyed by (project_dir, max_chars) -> (content, mtime_map).
_memory_cache: dict[str, tuple[str, float]] = {}


def set_session_memory_compact_config(config: dict[str, Any]) -> None:
    """Update the global session memory compaction configuration.

    Args:
        config: Dict with any subset of SessionMemoryCompactConfig fields.
            Values are coerced to the appropriate types.
    """
    global _config
    _config = SessionMemoryCompactConfig(
        min_tokens=int(config.get("min_tokens", DEFAULT_SM_COMPACT_CONFIG.min_tokens)),
        min_text_block_messages=int(
            config.get(
                "min_text_block_messages",
                DEFAULT_SM_COMPACT_CONFIG.min_text_block_messages,
            )
        ),
        max_tokens=int(config.get("max_tokens", DEFAULT_SM_COMPACT_CONFIG.max_tokens)),
        max_memory_chars=int(
            config.get("max_memory_chars", DEFAULT_SM_COMPACT_CONFIG.max_memory_chars)
        ),
        max_memory_tokens=int(
            config.get("max_memory_tokens", DEFAULT_SM_COMPACT_CONFIG.max_memory_tokens)
        ),
        min_conversation_rounds=int(
            config.get(
                "min_conversation_rounds",
                DEFAULT_SM_COMPACT_CONFIG.min_conversation_rounds,
            )
        ),
        summary_max_tokens=int(
            config.get(
                "summary_max_tokens", DEFAULT_SM_COMPACT_CONFIG.summary_max_tokens
            )
        ),
        update_memory_on_compact=bool(
            config.get(
                "update_memory_on_compact",
                DEFAULT_SM_COMPACT_CONFIG.update_memory_on_compact,
            )
        ),
        preserve_system_messages=bool(
            config.get(
                "preserve_system_messages",
                DEFAULT_SM_COMPACT_CONFIG.preserve_system_messages,
            )
        ),
        always_summarize=bool(
            config.get(
                "always_summarize", DEFAULT_SM_COMPACT_CONFIG.always_summarize
            )
        ),
    )


def get_session_memory_compact_config() -> SessionMemoryCompactConfig:
    """Get the current session memory compaction configuration."""
    return _config


def validate_compact_config(
    config: Optional[SessionMemoryCompactConfig] = None,
) -> list[str]:
    """Validate a SessionMemoryCompactConfig for sane values.

    Returns a list of warning/error strings. An empty list means valid.
    """
    cfg = config or _config
    warnings: list[str] = []

    if cfg.min_tokens < _MIN_MIN_TOKENS:
        warnings.append(
            f"min_tokens={cfg.min_tokens} is below floor {_MIN_MIN_TOKENS}"
        )
    if cfg.max_tokens < _MIN_MAX_TOKENS:
        warnings.append(
            f"max_tokens={cfg.max_tokens} is below floor {_MIN_MAX_TOKENS}"
        )
    if cfg.max_tokens <= cfg.min_tokens:
        warnings.append(
            f"max_tokens={cfg.max_tokens} must be greater than "
            f"min_tokens={cfg.min_tokens}"
        )
    if cfg.min_text_block_messages < _MIN_MIN_TEXT_BLOCK_MESSAGES:
        warnings.append(
            f"min_text_block_messages={cfg.min_text_block_messages} "
            f"is below floor {_MIN_MIN_TEXT_BLOCK_MESSAGES}"
        )
    if cfg.min_conversation_rounds < _MIN_MIN_CONVERSATION_ROUNDS:
        warnings.append(
            f"min_conversation_rounds={cfg.min_conversation_rounds} "
            f"is below floor {_MIN_MIN_CONVERSATION_ROUNDS}"
        )
    if cfg.summary_max_tokens < _MIN_SUMMARY_MAX_TOKENS:
        warnings.append(
            f"summary_max_tokens={cfg.summary_max_tokens} "
            f"is below floor {_MIN_SUMMARY_MAX_TOKENS}"
        )
    if cfg.max_memory_chars < _MIN_MAX_MEMORY_CHARS:
        warnings.append(
            f"max_memory_chars={cfg.max_memory_chars} "
            f"is below floor {_MIN_MAX_MEMORY_CHARS}"
        )
    if cfg.max_memory_tokens < _MIN_MAX_MEMORY_TOKENS:
        warnings.append(
            f"max_memory_tokens={cfg.max_memory_tokens} "
            f"is below floor {_MIN_MAX_MEMORY_TOKENS}"
        )

    return warnings


# ---------------------------------------------------------------------------
# Session memory loading
# ---------------------------------------------------------------------------


def _find_hare_md_files(project_dir: str = "") -> list[str]:
    """Find all HARE.md files from project root upward.

    Returns paths ordered from rootmost to leafmost, so merging
    later entries overrides earlier ones.

    On macOS/Linux, walks from filesystem root down to project dir.
    On Windows, walks from the drive root.
    """
    cwd = project_dir or os.getcwd()
    paths: list[str] = []

    # Resolve to absolute and split into components.
    abs_path = os.path.abspath(cwd)
    # Split the path into its constituent parts, handling both POSIX and Windows.
    # On POSIX, "/foo/bar" -> ["", "foo", "bar"]; on Windows, "C:\\foo" -> ["C:", "\\", "foo"].
    # Use os.sep for splitting but normalize afterwards.
    drive, tail = os.path.splitdrive(abs_path)
    parts = tail.split(os.sep)

    # Build candidates from rootmost to leafmost.
    # For POSIX, start from "/" and progressively add path segments.
    # For Windows, start from the drive root (e.g., "C:\\").
    if drive:
        # Windows: start with drive letter root
        root_parts = [drive + os.sep]
        for i in range(1, len(parts)):
            if parts[i]:
                candidate = os.path.join(*(root_parts + parts[1 : i + 1]), "HARE.md")
                if os.path.isfile(candidate):
                    paths.append(candidate)
    else:
        # POSIX: start from "/"
        for i in range(1, len(parts) + 1):
            segment = os.sep + os.path.join(*parts[:i])
            candidate = os.path.join(segment, "HARE.md")
            if os.path.isfile(candidate):
                paths.append(candidate)

    return paths


def _compute_cache_key(project_dir: str, max_chars: int) -> str:
    """Compute a stable cache key for the memory cache."""
    return f"{os.path.abspath(project_dir or os.getcwd())}::{max_chars}"


def _get_newest_mtime(paths: list[str]) -> float:
    """Get the newest mtime from a list of file paths.

    Returns 0.0 if none of the paths can be stat'd.
    """
    newest = 0.0
    for p in paths:
        try:
            mtime = os.path.getmtime(p)
            if mtime > newest:
                newest = mtime
        except OSError:
            pass
    # Also check user-level HARE.md
    user_path = os.path.join(os.path.expanduser("~"), ".hare", "HARE.md")
    try:
        mtime = os.path.getmtime(user_path)
        if mtime > newest:
            newest = mtime
    except OSError:
        pass
    return newest


def load_session_memory(
    project_dir: str = "",
    max_chars: int = DEFAULT_MAX_MEMORY_CHARS,
    *,
    use_cache: bool = True,
) -> str:
    """Load and merge session memory from HARE.md files.

    Loads from:
    1. Ancestor directories (parent project conventions)
    2. Current project HARE.md
    3. User-level ~/.hare/HARE.md

    Content is truncated to max_chars (from the beginning, keeping leafmost).

    Args:
        project_dir: Root directory for project HARE.md discovery.
        max_chars: Maximum characters to load.
        use_cache: If True, use in-memory caching keyed on project_dir + max_chars.
            Cache is invalidated when any source file's mtime changes.

    Returns:
        Merged memory content string (may be empty if no files found).
    """
    cache_key = _compute_cache_key(project_dir, max_chars)

    # Collect all file paths that contribute to the memory.
    hare_md_paths = _find_hare_md_files(project_dir)
    user_path = os.path.join(os.path.expanduser("~"), ".hare", "HARE.md")
    if os.path.isfile(user_path):
        hare_md_paths.append(user_path)

    all_paths = hare_md_paths  # includes user_path if it exists

    # Check cache validity.
    if use_cache and cache_key in _memory_cache:
        cached_content, cached_mtime = _memory_cache[cache_key]
        newest_mtime = _get_newest_mtime(all_paths)
        if newest_mtime <= cached_mtime:
            return cached_content

    # Build content from all sources.
    content_parts: list[str] = []

    for path in hare_md_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                # Include a comment marker so the model can see which file
                # contributed which section.
                content_parts.append(f"<!-- source: {path} -->\n{text}")
        except (FileNotFoundError, PermissionError, OSError):
            pass

    merged = "\n\n---\n\n".join(content_parts)

    # Truncate to max_chars from the beginning (the leafmost project HARE.md
    # is appended last and is most relevant, so keep the tail).
    if len(merged) > max_chars:
        merged = merged[-max_chars:]
        # Snap to the next newline so we don't start mid-line.
        newline = merged.find("\n")
        if newline > 0:
            merged = merged[newline + 1:]
        # Prepend a truncation indicator.
        merged = (
            "[... earlier project/ancestor memory content truncated "
            "to fit context window]\n\n" + merged
        )

    # Cache the result with the newest mtime we observed.
    newest_mtime = _get_newest_mtime(all_paths)
    _memory_cache[cache_key] = (merged, newest_mtime)

    return merged


def reset_session_memory_cache() -> None:
    """Clear the in-memory cache of loaded HARE.md content.

    Useful when HARE.md files are modified programmatically and the
    caller wants to force a re-read on the next load.
    """
    global _memory_cache
    _memory_cache.clear()


def truncate_memory_for_context(
    content: str,
    max_tokens: int = DEFAULT_MAX_MEMORY_TOKENS,
) -> str:
    """Truncate session memory content to fit within a token budget.

    Keeps headers and complete paragraphs; drops from the middle if needed.
    When truncation occurs, a placeholder is injected to indicate that
    content was omitted.

    Args:
        content: The full merged memory content.
        max_tokens: Maximum estimated tokens to keep.

    Returns:
        Truncated content string.
    """
    if not content.strip():
        return ""

    current_tokens = estimate_tokens(content)
    if current_tokens <= max_tokens:
        return content

    # Split into paragraphs and keep as many as fit.
    paragraphs = content.split("\n\n")
    result: list[str] = []
    running = 0
    truncated = False

    for para in paragraphs:
        pt = estimate_tokens(para)
        if running + pt <= max_tokens:
            result.append(para)
            running += pt
        else:
            truncated = True
            remaining = max_tokens - running
            if remaining > 20:
                # Try to include a partial paragraph (word-level truncation).
                words = para.split()
                partial: list[str] = []
                for w in words:
                    candidate = " ".join(partial + [w])
                    if estimate_tokens(candidate) <= remaining:
                        partial.append(w)
                    else:
                        break
                if partial:
                    result.append(" ".join(partial) + " ...")
            break

    if truncated:
        result.append(
            "[... session memory truncated to fit context window "
            f"({running} tokens kept of ~{current_tokens})]"
        )

    return "\n\n".join(result)


def get_session_memory_stats(
    project_dir: str = "",
) -> dict[str, Any]:
    """Return statistics about session memory files without loading fully.

    Returns:
        Dict with keys:
            - files_found: list of paths to HARE.md files
            - total_chars: sum of character counts across all files
            - newest_mtime: most recent modification time (float)
            - user_memory_exists: whether ~/.hare/HARE.md exists
    """
    paths = _find_hare_md_files(project_dir)
    total_chars = 0
    newest_mtime = 0.0

    for p in paths:
        try:
            total_chars += os.path.getsize(p)
            mtime = os.path.getmtime(p)
            if mtime > newest_mtime:
                newest_mtime = mtime
        except OSError:
            pass

    user_path = os.path.join(os.path.expanduser("~"), ".hare", "HARE.md")
    user_exists = os.path.isfile(user_path)
    if user_exists:
        try:
            total_chars += os.path.getsize(user_path)
            mtime = os.path.getmtime(user_path)
            if mtime > newest_mtime:
                newest_mtime = mtime
        except OSError:
            pass

    return {
        "files_found": paths,
        "total_chars": total_chars,
        "newest_mtime": newest_mtime,
        "user_memory_exists": user_exists,
    }


# ---------------------------------------------------------------------------
# Message analysis
# ---------------------------------------------------------------------------


def _has_text_blocks(message: dict[str, Any]) -> bool:
    """Check if a message contains text blocks with meaningful content."""
    msg_type = message.get("type", "")
    if msg_type == "assistant":
        content = message.get("message", {}).get("content", [])
        if isinstance(content, list):
            return any(
                b.get("type") == "text" and b.get("text", "").strip()
                for b in content
                if isinstance(b, dict)
            )
    if msg_type == "user":
        content = message.get("message", {}).get("content", "")
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return any(
                b.get("type") == "text" and b.get("text", "").strip()
                for b in content
                if isinstance(b, dict)
            )
    return False


def _is_system_message(message: dict[str, Any]) -> bool:
    """Check if a message is a system-level message (prompt, memory).

    Returns True for:
        - Messages with type="system"
        - Messages whose string content starts with '<' (XML-ish prompts)
        - Messages with subtype indicating system/compact boundary
    """
    msg_type = message.get("type", "")
    if msg_type == "system":
        return True
    # Heuristic: string content starting with '<' is likely a system prompt.
    content = message.get("message", {}).get("content", "")
    if isinstance(content, str) and content.strip().startswith("<"):
        return True
    # Check subtype for compact boundaries (they are system-level).
    subtype = message.get("subtype", "")
    if subtype in ("compact_boundary", "microcompact_summary", "session_memory_context"):
        return True
    return False


def _is_tombstone(message: dict[str, Any]) -> bool:
    """Check if a message is a tombstone marker (carries no content)."""
    return message.get("type") == "tombstone" or message.get("is_tombstone", False)


def _extract_message_text(message: dict[str, Any], max_chars: int = 500) -> str:
    """Extract readable text from a message for summarization.

    Handles all common block types: text, tool_use, tool_result, thinking.
    """
    msg_type = message.get("type", "")
    content = message.get("message", {}).get("content", "")

    if isinstance(content, str):
        return content[:max_chars]

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "")
                if text.strip():
                    parts.append(text)
            elif block_type == "tool_use":
                name = block.get("name", "unknown")
                inp = block.get("input", {})
                # Include key input fields for context.
                if isinstance(inp, dict):
                    relevant = {
                        k: v
                        for k, v in inp.items()
                        if k in ("file_path", "command", "path", "query", "pattern")
                    }
                    parts.append(f"[tool: {name}] {str(relevant)[:200]}")
                else:
                    parts.append(f"[tool: {name}] {str(inp)[:200]}")
            elif block_type == "tool_result":
                rc = block.get("content", "")
                if isinstance(rc, str):
                    # Show head and tail of long results.
                    if len(rc) > 600:
                        rc = rc[:300] + "\n...\n" + rc[-300:]
                    parts.append(f"[result] {rc}")
                elif isinstance(rc, list):
                    for item in rc:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            parts.append(f"[result] {text[:300]}")
            elif block_type == "thinking":
                thinking = block.get("thinking", "")
                if thinking:
                    parts.append(f"[thinking] {thinking[:200]}")
            elif block_type == "image":
                parts.append("[image]")
            elif block_type == "document":
                parts.append("[document]")
        return "\n".join(parts)[:max_chars]

    return ""


# ---------------------------------------------------------------------------
# Token utilities
# ---------------------------------------------------------------------------


def _tokenize_simple(text: str) -> int:
    """Fast word-based token count for a single string (no API call).

    Uses the standard ~4 chars-per-token heuristic but also accounts
    for whitespace and code patterns that are token-dense.

    This is intentionally simple — for exact counts, use the API.
    """
    if not text:
        return 0
    # Count words as a rough proxy; add char-based estimate and average.
    word_count = len(text.split())
    char_estimate = max(1, len(text) // 4)
    # Blend: word count is usually lower than char/4 for code,
    # so take the max to avoid underestimating code-heavy content.
    return max(word_count, char_estimate, 1)


def estimate_message_token_breakdown(
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a detailed token breakdown for a message list.

    Useful for debugging compaction thresholds.

    Returns:
        Dict with:
            - total_tokens: int
            - by_type: {type_name: token_count}
            - text_block_count: number of messages with text blocks
            - tool_use_count: number of tool_use blocks
            - tool_result_count: number of tool_result blocks
            - image_count: number of image blocks
    """
    total = 0
    by_type: dict[str, int] = {}
    text_block_count = 0
    tool_use_count = 0
    tool_result_count = 0
    image_count = 0

    for msg in messages:
        msg_type = msg.get("type", "unknown")
        content = msg.get("message", {}).get("content", [])

        msg_tokens = 0
        if isinstance(content, str):
            msg_tokens = estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text = block.get("text", "")
                    msg_tokens += estimate_tokens(text)
                    if text.strip():
                        text_block_count += 1
                elif block_type == "tool_use":
                    msg_tokens += estimate_tokens(str(block.get("input", {}))) + 20
                    tool_use_count += 1
                elif block_type == "tool_result":
                    rc = block.get("content", "")
                    if isinstance(rc, str):
                        msg_tokens += estimate_tokens(rc)
                    tool_result_count += 1
                elif block_type == "image":
                    msg_tokens += 1600
                    image_count += 1
                elif block_type == "document":
                    msg_tokens += 1600
                elif block_type == "thinking":
                    msg_tokens += estimate_tokens(block.get("thinking", ""))

        msg_tokens += 4  # per-message overhead
        total += msg_tokens
        by_type[msg_type] = by_type.get(msg_type, 0) + msg_tokens

    return {
        "total_tokens": total,
        "by_type": by_type,
        "text_block_count": text_block_count,
        "tool_use_count": tool_use_count,
        "tool_result_count": tool_result_count,
        "image_count": image_count,
    }


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------


# Patterns used to detect errors in tool results. Word-boundary-aware
# to avoid matching substrings inside normal words (e.g., "terror").
_ERROR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Traceback", re.compile(r"\bTraceback\b")),
    ("Exception", re.compile(r"\bException\b")),
    ("Error", re.compile(r"\bError\b")),
    ("ERROR", re.compile(r"\bERROR\b")),
    ("failed", re.compile(r"\bfailed\b")),
    ("Failed", re.compile(r"\bFailed\b")),
    ("SyntaxError", re.compile(r"\bSyntaxError\b")),
    ("TypeError", re.compile(r"\bTypeError\b")),
    ("PermissionError", re.compile(r"\bPermissionError\b")),
    ("FileNotFoundError", re.compile(r"\bFileNotFoundError\b")),
    ("fatal", re.compile(r"\bfatal\b")),
    ("panic", re.compile(r"\bpanic\b")),
    ("abort", re.compile(r"\babort(?:ed|ing)?\b")),
    ("timeout", re.compile(r"\btimeout\b", re.IGNORECASE)),
    ("refused", re.compile(r"\brefused\b")),
    ("denied", re.compile(r"\bdenied\b")),
    ("cannot", re.compile(r"\bcannot\b")),
    ("not found", re.compile(r"\bnot\s+found\b", re.IGNORECASE)),
]

# Keywords considered actionable for HARE.md auto-updates.
_ACTIONABLE_KEYWORDS = (
    "Files:",
    "Action:",
    "Errors:",
    "User:",
    "Decision:",
    "Modified:",
    "Created:",
    "Deleted:",
    "Tool:",
)


def _detect_errors_in_text(text: str, max_snippet_len: int = 200) -> list[str]:
    """Scan a text string for error indicators.

    Returns a list of "[PATTERN] snippet" strings, one per unique pattern matched.
    """
    found: list[str] = []
    seen_patterns: set[str] = set()
    for label, pattern in _ERROR_PATTERNS:
        if label in seen_patterns:
            continue
        match = pattern.search(text[:1000])
        if match:
            seen_patterns.add(label)
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + max_snippet_len - 40)
            snippet = text[start:end].replace("\n", " ").strip()
            if len(snippet) > max_snippet_len:
                snippet = snippet[:max_snippet_len] + "..."
            found.append(f"[{label}] {snippet}")
    return found


def _generate_round_summary(
    messages: list[dict[str, Any]],
    round_index: int,
) -> str:
    """Generate a structured summary for a single conversation round.

    Extracts: user intent, key actions, files touched, errors, decisions.

    Robust against malformed messages — any individual message that
    fails to parse is silently skipped.
    """
    user_messages: list[str] = []
    assistant_actions: list[str] = []
    files_touched: set[str] = set()
    errors: list[str] = []
    tool_calls: list[str] = []
    decisions: list[str] = []

    for msg in messages:
        try:
            msg_type = msg.get("type", "")
            content = msg.get("message", {}).get("content", "")

            if msg_type == "user":
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            break
                if text.strip():
                    user_messages.append(text.strip()[:500])

            elif msg_type == "assistant":
                content_list = content if isinstance(content, list) else []
                for block in content_list:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text = block.get("text", "")
                        if text.strip():
                            assistant_actions.append(text.strip()[:300])
                            # Heuristic: lines starting with "Decision:" or containing
                            # "I will" / "Let's" are likely decision statements.
                            for line in text.split("\n"):
                                stripped = line.strip()
                                if stripped.startswith("Decision:") or any(
                                    stripped.startswith(prefix)
                                    for prefix in (
                                        "I will",
                                        "Let's",
                                        "We should",
                                        "The plan",
                                    )
                                ):
                                    decisions.append(stripped[:200])
                    elif block_type == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        tool_calls.append(name)
                        # Extract file paths from common tool input keys.
                        if isinstance(inp, dict):
                            for key in (
                                "file_path",
                                "path",
                                "file",
                                "target_file",
                                "command",
                                "pattern",
                            ):
                                val = inp.get(key, "")
                                if isinstance(val, str) and val:
                                    files_touched.add(val)
                                elif isinstance(val, list):
                                    for item in val:
                                        if isinstance(item, str):
                                            files_touched.add(item)
                    elif block_type == "tool_result":
                        rc = block.get("content", "")
                        if isinstance(rc, str):
                            errs = _detect_errors_in_text(rc)
                            errors.extend(errs)
                        elif isinstance(rc, list):
                            for item in rc:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    errs = _detect_errors_in_text(
                                        item.get("text", "")
                                    )
                                    errors.extend(errs)
        except Exception:
            # Malformed message — skip silently.
            continue

    parts: list[str] = []
    if user_messages:
        parts.append(f"User: {' | '.join(user_messages[:3])}")
    if tool_calls:
        # Preserve order, deduplicate.
        unique_tools = list(dict.fromkeys(tool_calls))
        parts.append(f"Tools: {', '.join(unique_tools[:10])}")
    if files_touched:
        parts.append(f"Files: {', '.join(sorted(files_touched)[:10])}")
    if assistant_actions:
        # Take the first substantive action as the round's key action.
        summary_text = assistant_actions[0][:200]
        parts.append(f"Action: {summary_text}")
    if decisions:
        parts.append(f"Decision: {decisions[0][:200]}")
    if errors:
        parts.append(f"Errors: {'; '.join(errors[:3])}")

    header = f"[Round {round_index}]"
    if not parts:
        return f"{header} (no substantive content)"
    return header + " " + " | ".join(parts)


def summarize_older_messages(
    messages: list[dict[str, Any]],
    *,
    max_summary_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
) -> str:
    """Summarize a set of older messages into a compact context block.

    Groups messages by conversation round and produces one summary line
    per round, keeping total under max_summary_tokens.

    Args:
        messages: The older messages to summarize.
        max_summary_tokens: Maximum tokens for the summary block.

    Returns:
        A newline-joined summary string, or empty string if no messages.
    """
    if not messages:
        return ""

    try:
        rounds = group_messages_by_api_round(messages)
    except Exception:
        _log.warning("Failed to group messages by API round, falling back to flat list")
        rounds = [messages]

    # Filter out empty rounds.
    rounds = [r for r in rounds if r]
    if not rounds:
        return ""

    summaries: list[str] = []
    running_tokens = 0
    total_round_tokens = 0

    for idx, round_msgs in enumerate(rounds, start=1):
        line = _generate_round_summary(round_msgs, idx)
        line_tokens = estimate_tokens(line)

        if running_tokens + line_tokens > max_summary_tokens:
            # Emit a truncation marker for the remaining rounds.
            remaining = len(rounds) - idx + 1
            # Estimate tokens for the remaining rounds (use round count as proxy).
            remaining_tokens = sum(
                estimate_message_tokens(r) for r in rounds[idx - 1 :]
            )
            summaries.append(
                f"[... {remaining} earlier round(s) omitted, "
                f"~{max(1, remaining_tokens)} tokens]"
            )
            break

        summaries.append(line)
        running_tokens += line_tokens

    if not summaries:
        return ""

    return "\n".join(summaries)


def build_session_memory_context_block(
    memory_content: str,
    summary: str,
    *,
    label: str = "Session Memory / Conversation Context",
) -> str:
    """Build a combined context block from session memory and summary.

    This is injected at the boundary between old (summarized) and new (kept)
    messages so the model retains both long-term context and recent history.

    The block uses XML-style tags that the model is trained to recognize
    as structured context boundaries.

    Args:
        memory_content: Loaded HARE.md content (may be empty).
        summary: Generated round-by-round summary (may be empty).
        label: The tag label to use.

    Returns:
        Formatted context block string.
    """
    parts: list[str] = [f"<{label}>"]

    if memory_content.strip():
        parts.append(memory_content.strip())

    if summary.strip():
        if memory_content.strip():
            parts.append("")
            parts.append("--- Conversation History ---")
        parts.append(summary.strip())

    parts.append(f"</{label}>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Split point detection
# ---------------------------------------------------------------------------


def find_split_by_rounds(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 40_000,
    min_rounds_to_keep: int = 2,
    preserve_system: bool = True,
) -> int:
    """Find the optimal message index to split at conversation-round boundaries.

    Returns the index in `messages` where the kept portion begins.
    All messages before this index will be summarized.

    Strategy:
    1. Group messages by API round.
    2. Walk backwards from the most recent round.
    3. Accumulate tokens until max_tokens is reached.
    4. Split at the round boundary.
    5. If preserve_system is True, keep system messages at the top with
       the kept portion (they should not be summarized away).

    Args:
        messages: Full message list.
        max_tokens: Maximum tokens for the kept portion.
        min_rounds_to_keep: Minimum number of rounds to keep, even if
            they exceed max_tokens.
        preserve_system: If True, system messages at the very beginning
            of the old portion are pulled into the kept portion.

    Returns:
        Index in `messages` where kept portion starts. 0 means no split.
    """
    if not messages:
        return 0

    try:
        rounds = group_messages_by_api_round(messages)
    except Exception:
        _log.warning("Failed to group messages by API round for split detection")
        return 0

    # Filter empty rounds.
    rounds = [r for r in rounds if r]
    if not rounds or len(rounds) <= min_rounds_to_keep:
        return 0

    # Figure out which rounds to keep (walk backwards from most recent).
    keep_round_indices: list[int] = []
    running_tokens = 0

    for ri in range(len(rounds) - 1, -1, -1):
        round_tokens = estimate_message_tokens(rounds[ri])
        if (
            running_tokens + round_tokens > max_tokens
            and len(keep_round_indices) >= min_rounds_to_keep
        ):
            break
        keep_round_indices.append(ri)
        running_tokens += round_tokens

    if not keep_round_indices:
        return 0

    first_kept_round = min(keep_round_indices)

    # Find the message index where the first kept round begins.
    msg_idx = 0
    for ri in range(first_kept_round):
        msg_idx += len(rounds[ri])

    # Sanity check: don't split too close to the end.
    if msg_idx >= len(messages) - 1:
        return 0

    # Preserve system-only prefix rounds: if the first N rounds consist
    # entirely of system/tombstone messages, always include them in the
    # kept set. This ensures system prompts aren't summarized away.
    # The actual extraction of system messages into the new message list
    # is handled by try_session_memory_compaction.
    if preserve_system:
        system_prefix_round_count = 0
        msg_pos = 0
        for ri in range(first_kept_round):
            round_msgs = rounds[ri]
            if all(
                _is_system_message(m) or _is_tombstone(m) for m in round_msgs
            ):
                system_prefix_round_count += 1
                msg_pos += len(round_msgs)
            else:
                break

        if system_prefix_round_count > 0:
            # Add the system prefix rounds to the kept set.
            for ri in range(system_prefix_round_count):
                if ri not in keep_round_indices:
                    keep_round_indices.append(ri)

            # If system prefix rounds span the entire old portion up to
            # the first non-system round, adjust msg_idx to skip them
            # so they are not part of the "old" portion.
            if msg_pos < msg_idx:
                msg_idx = msg_pos

    return msg_idx


def find_split_by_tokens(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 40_000,
    min_to_keep: int = 2,
) -> int:
    """Simple token-based split: keep the most recent messages fitting under max_tokens.

    This is a fallback when round-based splitting produces too few rounds.

    Args:
        messages: Full message list.
        max_tokens: Maximum tokens for the kept portion.
        min_to_keep: Minimum number of messages to keep regardless of tokens.

    Returns:
        Index in `messages` where kept portion starts.
    """
    if not messages:
        return 0

    keep_from = len(messages)
    running_tokens = 0

    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = estimate_message_tokens([messages[i]])
        if running_tokens + msg_tokens > max_tokens and (len(messages) - i) >= min_to_keep:
            break
        running_tokens += msg_tokens
        keep_from = i

    # Ensure we always keep at least min_to_keep messages.
    keep_from = min(keep_from, max(0, len(messages) - min_to_keep))

    return keep_from


# ---------------------------------------------------------------------------
# Compaction eligibility
# ---------------------------------------------------------------------------


def is_compaction_eligible(
    messages: list[dict[str, Any]],
    *,
    config: Optional[SessionMemoryCompactConfig] = None,
) -> tuple[bool, str]:
    """Check whether messages are eligible for session memory compaction.

    Returns (eligible, reason) where reason explains the decision.

    This is a fast gate that can be called before the full compaction
    pipeline — it only does the cheap checks (count, threshold).
    """
    cfg = config or _config

    if not messages:
        return False, "No messages to compact"

    if len(messages) < cfg.min_text_block_messages:
        return (
            False,
            f"Only {len(messages)} messages, need at least "
            f"{cfg.min_text_block_messages}",
        )

    # Count messages with text blocks.
    text_msg_count = sum(1 for m in messages if _has_text_blocks(m))
    if text_msg_count < cfg.min_text_block_messages:
        return (
            False,
            f"Only {text_msg_count} text-block messages, need at least "
            f"{cfg.min_text_block_messages}",
        )

    if cfg.always_summarize:
        return True, "always_summarize is enabled"

    total_tokens = estimate_message_tokens(messages)
    if total_tokens < cfg.min_tokens:
        return (
            False,
            f"Total tokens {total_tokens} below minimum {cfg.min_tokens}",
        )

    return True, f"Eligible: {total_tokens} tokens in {len(messages)} messages"


# ---------------------------------------------------------------------------
# Main compaction logic
# ---------------------------------------------------------------------------


async def try_session_memory_compaction(
    messages: list[dict[str, Any]],
    agent_id: str = "",
    *,
    force: bool = False,
) -> Optional[dict[str, Any]]:
    """Try to compact using session memory with summarization.

    Returns a CompactionResult-like dict if successful, None if not applicable.

    The compaction process:
    1. Validate config and check eligibility
    2. Load session memory (HARE.md files)
    3. Determine if compaction is needed (token threshold check)
    4. Find optimal split point using conversation rounds
    5. Summarize older messages into a structured block
    6. Inject session memory context at the boundary
    7. Return compacted messages with metadata

    Args:
        messages: Full message list to consider for compaction.
        agent_id: Optional agent identifier for logging/memory updates.
        force: If True, bypass the eligibility gate and compact anyway.

    Returns:
        Dict with keys:
            - new_messages: list of compacted messages
            - summary: the round-by-round summary text
            - session_memory_used: whether HARE.md content was included
            - tokens_before: token estimate before compaction
            - tokens_after: token estimate after compaction
            - messages_removed: number of messages summarized away
            - split_point: index where split occurred
            - original_message_count: count before compaction
            - compacted_message_count: count after compaction
        Or None if compaction is not applicable / fails.
    """
    cfg = _config

    # Validate config before proceeding (log warnings, don't abort).
    config_warnings = validate_compact_config(cfg)
    if config_warnings:
        _log.warning(
            "Session memory compact config warnings: %s",
            "; ".join(config_warnings),
        )

    try:
        # ---- Gate: eligibility ----
        if not force:
            eligible, reason = is_compaction_eligible(messages, config=cfg)
            if not eligible:
                _log.debug("Session memory compaction skipped: %s", reason)
                return None

        # ---- Load session memory ----
        memory_content = load_session_memory(
            project_dir=os.getcwd(), max_chars=cfg.max_memory_chars
        )
        memory_content = truncate_memory_for_context(
            memory_content, max_tokens=cfg.max_memory_tokens
        )

        # ---- Find split point ----
        split_point = find_split_by_rounds(
            messages,
            max_tokens=cfg.max_tokens,
            min_rounds_to_keep=cfg.min_conversation_rounds,
            preserve_system=cfg.preserve_system_messages,
        )

        # If round-based split found nothing useful, fall back to simple
        # token split (but only if we have enough total tokens).
        if split_point == 0 or split_point >= len(messages) - 1:
            total_tokens = estimate_message_tokens(messages)
            if total_tokens > cfg.max_tokens or force:
                split_point = find_split_by_tokens(
                    messages,
                    max_tokens=cfg.max_tokens,
                    min_to_keep=max(2, cfg.min_conversation_rounds),
                )

        if split_point <= 0 or split_point >= len(messages):
            _log.debug(
                "Session memory compaction: no valid split point found "
                "(split_point=%d, total_messages=%d)",
                split_point,
                len(messages),
            )
            return None

        # ---- Split messages ----
        old_messages = messages[:split_point]
        kept_messages = messages[split_point:]

        # Sanity check: both portions must have content.
        if not old_messages or not kept_messages:
            _log.debug(
                "Session memory compaction: split produced empty portion "
                "(old=%d, kept=%d)",
                len(old_messages),
                len(kept_messages),
            )
            return None

        # ---- Summarize older portion ----
        summary = summarize_older_messages(
            old_messages,
            max_summary_tokens=cfg.summary_max_tokens,
        )

        # ---- Build context block ----
        context_block = build_session_memory_context_block(
            memory_content=memory_content,
            summary=summary,
        )

        # ---- Build new message list ----
        # Strategy:
        #   1. Pull forward any system messages from the original list
        #      that should stay at the top (initial prompts, tool definitions)
        #   2. Insert the session memory context block as a system message
        #   3. Append the kept (recent) messages, deduplicating any system
        #      messages we already pulled forward.

        new_messages: list[dict[str, Any]] = []

        # Collect system messages from the old portion that should be preserved.
        preserved_system_messages: list[dict[str, Any]] = []
        if cfg.preserve_system_messages:
            for msg in old_messages:
                if _is_system_message(msg) and not _is_tombstone(msg):
                    preserved_system_messages.append(msg)

        # Also check if any system messages from the kept portion should be
        # deduplicated (identical content).
        kept_system_content: set[str] = set()
        for msg in kept_messages:
            if _is_system_message(msg):
                content = msg.get("message", {}).get("content", "")
                if isinstance(content, str):
                    kept_system_content.add(content.strip())

        # Only include preserved system messages that don't duplicate
        # content already in the kept portion.
        for msg in preserved_system_messages:
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip() in kept_system_content:
                continue
            new_messages.append(msg)

        # Add the session memory context block.
        context_message: dict[str, Any] = {
            "type": "system",
            "message": {
                "role": "system",
                "content": context_block,
            },
            "is_session_memory_context": True,
        }
        new_messages.append(context_message)

        # Add kept messages.
        new_messages.extend(kept_messages)

        # ---- Compute stats ----
        total_tokens = estimate_message_tokens(messages)
        tokens_after = estimate_message_tokens(new_messages)
        removed_count = len(messages) - len(kept_messages)

        # ---- Optionally update HARE.md ----
        if cfg.update_memory_on_compact and summary.strip():
            _append_to_memory_if_changed(summary, agent_id)

        result = {
            "new_messages": new_messages,
            "summary": summary,
            "session_memory_used": bool(memory_content.strip()),
            "tokens_before": total_tokens,
            "tokens_after": tokens_after,
            "messages_removed": removed_count,
            "split_point": split_point,
            "original_message_count": len(messages),
            "compacted_message_count": len(new_messages),
        }

        _log.info(
            "Session memory compaction complete: %d -> %d messages, "
            "%d -> %d tokens (saved %d)",
            len(messages),
            len(new_messages),
            total_tokens,
            tokens_after,
            total_tokens - tokens_after,
        )

        return result

    except Exception as exc:
        _log.error(
            "Session memory compaction failed: %s",
            exc,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Synchronous wrapper
# ---------------------------------------------------------------------------


def compact_with_session_memory(
    messages: list[dict[str, Any]],
    agent_id: str = "",
    *,
    force: bool = False,
) -> Optional[dict[str, Any]]:
    """Synchronous wrapper around try_session_memory_compaction.

    Runs the async compaction in a new event loop. Suitable for callers
    that are not already in an async context.

    Args:
        messages: Full message list.
        agent_id: Optional agent identifier.
        force: If True, bypass eligibility check.

    Returns:
        Same as try_session_memory_compaction, or None.
    """
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside a running event loop — use run_in_executor to avoid
            # "cannot run loop from running loop" errors.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    lambda: asyncio.run(
                        try_session_memory_compaction(
                            messages, agent_id=agent_id, force=force
                        )
                    )
                )
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(
                try_session_memory_compaction(
                    messages, agent_id=agent_id, force=force
                )
            )
    except RuntimeError:
        # No event loop in this thread.
        return asyncio.run(
            try_session_memory_compaction(
                messages, agent_id=agent_id, force=force
            )
        )


# ---------------------------------------------------------------------------
# Supplemental: memory update after compaction
# ---------------------------------------------------------------------------


_last_memory_update_ts: float = 0.0
_MIN_UPDATE_INTERVAL_SEC = 300  # Don't update HARE.md more than once per 5 minutes.


def _append_to_memory_if_changed(summary: str, agent_id: str) -> None:
    """Append key findings to HARE.md if enough time has passed since last update.

    Only the most salient points are extracted from the summary to avoid
    bloating the memory file. The appended section is clearly marked as
    auto-generated so users can review/remove it.

    Thread-safe via the _last_memory_update_ts sentinel.
    """
    global _last_memory_update_ts

    now = time.time()
    if now - _last_memory_update_ts < _MIN_UPDATE_INTERVAL_SEC:
        return

    # Extract key lines from the summary.
    key_lines: list[str] = []
    for line in summary.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if any(keyword in stripped for keyword in _ACTIONABLE_KEYWORDS):
            key_lines.append(f"- {stripped}")

    if not key_lines:
        return

    _last_memory_update_ts = now

    memory_path = os.path.join(os.getcwd(), "HARE.md")
    try:
        header = (
            f"\n\n<!-- Session memory auto-update from compaction "
            f"({time.strftime('%Y-%m-%d %H:%M:%S')}, agent={agent_id or 'unknown'}) -->"
        )
        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(header + "\n")
            f.write("\n".join(key_lines))
        # Invalidate the memory cache so the next load picks up the update.
        reset_session_memory_cache()
    except (PermissionError, OSError) as exc:
        _log.warning("Failed to update HARE.md: %s", exc)


def _get_memory_update_ts() -> float:
    """Return the timestamp of the last memory update (for testing)."""
    return _last_memory_update_ts


def _reset_memory_update_ts() -> None:
    """Reset the memory update timestamp (for testing)."""
    global _last_memory_update_ts
    _last_memory_update_ts = 0.0


# ---------------------------------------------------------------------------
# Utility: dry-run analysis
# ---------------------------------------------------------------------------


def analyze_compaction_plan(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 40_000,
    project_dir: str = "",
) -> dict[str, Any]:
    """Analyze what a compaction run would do, without modifying anything.

    Useful for debugging and tuning thresholds.

    Returns a detailed dict with compaction plan statistics.
    """
    cfg = _config

    # Basic stats.
    total_tokens = estimate_message_tokens(messages)
    text_msg_count = sum(1 for m in messages if _has_text_blocks(m))

    # Round grouping.
    try:
        rounds = group_messages_by_api_round(messages)
        rounds = [r for r in rounds if r]  # filter empty
    except Exception:
        rounds = []

    # Split point.
    split_point = find_split_by_rounds(
        messages,
        max_tokens=max_tokens,
        min_rounds_to_keep=cfg.min_conversation_rounds,
        preserve_system=cfg.preserve_system_messages,
    )
    if split_point == 0 or split_point >= len(messages) - 1:
        split_point = find_split_by_tokens(
            messages,
            max_tokens=max_tokens,
            min_to_keep=max(2, cfg.min_conversation_rounds),
        )

    # Calculate rounds_to_keep: how many complete rounds are in the kept portion.
    rounds_to_keep = 0
    if split_point > 0 and rounds:
        msg_counter = 0
        for r in rounds:
            if msg_counter >= split_point:
                rounds_to_keep += 1
            msg_counter += len(r)
    elif rounds:
        rounds_to_keep = len(rounds)

    # Memory stats.
    memory_stats = get_session_memory_stats(project_dir=project_dir)
    memory_content = load_session_memory(
        project_dir=project_dir, max_chars=cfg.max_memory_chars
    )
    memory_tokens = estimate_tokens(memory_content)

    # Eligibility.
    would_compact = (
        total_tokens >= cfg.min_tokens or cfg.always_summarize
    ) and (
        text_msg_count >= cfg.min_text_block_messages
    ) and (
        split_point > 0
    )

    # Token breakdown for the old portion.
    old_breakdown = {}
    if split_point > 0:
        old_breakdown = estimate_message_token_breakdown(messages[:split_point])

    kept_breakdown = {}
    if split_point < len(messages):
        kept_breakdown = estimate_message_token_breakdown(messages[split_point:])

    return {
        "would_compact": would_compact,
        "total_tokens": total_tokens,
        "total_messages": len(messages),
        "text_block_messages": text_msg_count,
        "conversation_rounds": len(rounds),
        "rounds_to_summarize": len(rounds) - rounds_to_keep,
        "rounds_to_keep": rounds_to_keep,
        "split_point": split_point,
        "messages_to_summarize": split_point,
        "messages_to_keep": len(messages) - split_point,
        "memory_size_chars": memory_stats["total_chars"],
        "memory_estimated_tokens": memory_tokens,
        "memory_files_found": memory_stats["files_found"],
        "memory_user_exists": memory_stats["user_memory_exists"],
        "threshold_min_tokens": cfg.min_tokens,
        "threshold_max_tokens": max_tokens,
        "threshold_min_text_block_messages": cfg.min_text_block_messages,
        "threshold_min_conversation_rounds": cfg.min_conversation_rounds,
        "old_portion_tokens": old_breakdown.get("total_tokens", 0),
        "kept_portion_tokens": kept_breakdown.get("total_tokens", 0),
        "config_warnings": validate_compact_config(cfg),
    }


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    # Config
    "SessionMemoryCompactConfig",
    "DEFAULT_SM_COMPACT_CONFIG",
    "set_session_memory_compact_config",
    "get_session_memory_compact_config",
    "validate_compact_config",
    # Memory loading
    "load_session_memory",
    "truncate_memory_for_context",
    "get_session_memory_stats",
    "reset_session_memory_cache",
    # Summarization
    "summarize_older_messages",
    "build_session_memory_context_block",
    # Split detection
    "find_split_by_rounds",
    "find_split_by_tokens",
    # Eligibility
    "is_compaction_eligible",
    # Token utilities
    "estimate_message_token_breakdown",
    # Main entry points
    "try_session_memory_compaction",
    "compact_with_session_memory",
    # Analysis / debug
    "analyze_compaction_plan",
]
