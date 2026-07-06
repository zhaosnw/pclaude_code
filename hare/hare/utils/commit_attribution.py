"""Git commit attribution tracking (`commitAttribution.ts`). — core helpers.

Provides:
  - Model-name sanitization (surface keys, model shortnames).
  - Repo classification (internal vs public).
  - Per-session `AttributionState` tracking (file baselines, prompt / escape
    counters, starting HEAD).
  - Full `calculate_commit_attribution()` that merges session state with git
    diff data to produce character-level Claude-vs-human attribution.
  - Footer generation for "Co-Authored-By:" / undercover-switched footers.
  - State serialization for persistence across sessions.
  - Counter increment / reset helpers for commit-time snapshots.
  - Binary / renamed / deleted file handling in diff attribution.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Internal repos used for undercover classification
# ---------------------------------------------------------------------------

INTERNAL_MODEL_REPOS: tuple[str, ...] = (
    "github.com:anthropics/claude-cli-internal",
    "github.com/anthropics/claude-cli-internal",
    "github.com:anthropics/anthropic",
    "github.com/anthropics/anthropic",
)

# Minimum percentage of Claude-authored characters in total diff to qualify
# a commit for the Co-Authored-By footer (default 10 %).
_DEFAULT_MIN_CLAUDE_PERCENT = 10

# Minimum character threshold for attribution (avoids "trivial" attributions).
_DEFAULT_MIN_CLAUDE_CHARS = 50

# ---------------------------------------------------------------------------
# Sanitization helpers: model-name / surface-key
# ---------------------------------------------------------------------------


def sanitize_model_name(short_name: str) -> str:
    """Map internal model short-names to public family names.

    >>> sanitize_model_name("claude-opus-4-6-20250514")
    'claude-opus-4-6'
    """
    # Check from most specific to least specific to avoid greedy prefix matches
    if "opus-4-6" in short_name:
        return "claude-opus-4-6"
    if "opus-4-5" in short_name:
        return "claude-opus-4-5"
    if "opus-4-1" in short_name:
        return "claude-opus-4-1"
    if "opus-4" in short_name:
        return "claude-opus-4"
    if "sonnet-4-6" in short_name:
        return "claude-sonnet-4-6"
    if "sonnet-4-5" in short_name:
        return "claude-sonnet-4-5"
    if "sonnet-4" in short_name:
        return "claude-sonnet-4"
    if "sonnet-3-7" in short_name:
        return "claude-sonnet-3-7"
    if "haiku-4-5" in short_name:
        return "claude-haiku-4-5"
    if "haiku-3-5" in short_name:
        return "claude-haiku-3-5"
    # Fallback for unknown / unrecognized model names
    return "hare"


# Cache for already-sanitized surface keys to avoid repeated splitting.
_surface_key_cache: dict[str, str] = {}


def sanitize_surface_key(surface_key: str) -> str:
    """Sanitize a composite surface/model key (e.g. 'cli/claude-opus-4-6-20250514').

    >>> sanitize_surface_key("cli/claude-opus-4-6-20250514")
    'cli/claude-opus-4-6'
    """
    if surface_key in _surface_key_cache:
        return _surface_key_cache[surface_key]
    idx = surface_key.rfind("/")
    if idx == -1:
        result = surface_key
    else:
        surface, model = surface_key[:idx], surface_key[idx + 1 :]
        result = f"{surface}/{sanitize_model_name(model)}"
    _surface_key_cache[surface_key] = result
    return result


def compute_content_hash(content: str) -> str:
    """SHA-256 hex digest of *content* (used as file-baseline fingerprint)."""
    if not content:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def compute_content_hash_bytes(content: bytes) -> str:
    """SHA-256 hex digest for binary content."""
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Client surface detection
# ---------------------------------------------------------------------------


def get_client_surface() -> str:
    """Return the client surface identifier.

    Reads ``CLAUDE_CODE_ENTRYPOINT`` env var, defaulting to ``"cli"``.
    Falls back to ``CLAUDE_CODE_SURFACE`` if entrypoint is not set.
    """
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if entrypoint:
        return entrypoint.strip()
    surface = os.environ.get("CLAUDE_CODE_SURFACE", "")
    if surface:
        return surface.strip()
    # Detect VSCode/JetBrains presence as a heuristic
    if os.environ.get("VSCODE_PID") or os.environ.get("VSCODE_CWD"):
        return "vscode"
    return "cli"


# ---------------------------------------------------------------------------
# Repo classification (internal vs public)
# ---------------------------------------------------------------------------

_remote_cache: dict[tuple[str], str] = {}


def _get_git_remote_url(cwd: str | None = None) -> str:
    """Return the primary remote origin URL, or ``""`` if unavailable.

    Results are cached per cwd to avoid repeated subprocess calls.
    """
    root_key = (cwd or os.getcwd(),)
    if root_key in _remote_cache:
        return _remote_cache[root_key]

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=cwd or os.getcwd(),
            timeout=5,
        )
        url = result.stdout.strip() if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        url = ""
    except Exception:
        url = ""

    _remote_cache[root_key] = url
    return url


def _clear_remote_cache() -> None:
    """Clear the git remote URL cache (useful for tests)."""
    _remote_cache.clear()


def _normalise_repo_url(url: str) -> str:
    """Strip protocol / user / trailing ``.git`` so comparison is stable."""
    s = url.strip()
    # Remove protocol prefixes
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^ssh://", "", s)
    s = re.sub(r"^git@", "", s)
    # Replace colon after known hosts with slash (ssh-style)
    s = re.sub(r"^(github\.com):", r"\1/", s)
    s = re.sub(r"^(gitlab\.com):", r"\1/", s)
    s = re.sub(r"^(bitbucket\.org):", r"\1/", s)
    # Strip trailing .git
    s = re.sub(r"\.git$", "", s)
    # Strip trailing slash
    s = s.rstrip("/")
    return s


def classify_repo(cwd: str | None = None) -> str:
    """Return ``"internal"`` if the origin remote matches `INTERNAL_MODEL_REPOS`,
    otherwise ``"public"``.

    A missing remote or non-git directory is treated as ``"public"``.
    """
    url = _get_git_remote_url(cwd)
    if not url:
        return "public"
    norm = _normalise_repo_url(url)
    for pat in INTERNAL_MODEL_REPOS:
        if norm.startswith(_normalise_repo_url(pat)):
            return "internal"
    return "public"


@lru_cache(maxsize=1)
def get_repo_class_cached() -> str:
    """Cached wrapper around `classify_repo()`.

    Called by `undercover.is_undercover()` — must exist with this exact name.
    """
    return classify_repo()


# ---------------------------------------------------------------------------
# File-attribution data
# ---------------------------------------------------------------------------


@dataclass
class FileAttribution:
    """Per-file character-level attribution."""

    file_path: str = ""
    claude_chars: int = 0
    human_chars: int = 0
    claude_lines_added: int = 0
    human_lines_added: int = 0
    total_lines_added: int = 0
    claude_percent: float = 0.0
    is_new_file: bool = False
    is_binary: bool = False
    is_generated: bool = False
    is_deleted: bool = False
    is_renamed: bool = False
    rename_from: str = ""
    rename_to: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "filePath": self.file_path,
            "claudeChars": self.claude_chars,
            "humanChars": self.human_chars,
            "claudeLinesAdded": self.claude_lines_added,
            "humanLinesAdded": self.human_lines_added,
            "totalLinesAdded": self.total_lines_added,
            "claudePercent": self.claude_percent,
            "isNewFile": self.is_new_file,
            "isBinary": self.is_binary,
            "isGenerated": self.is_generated,
            "isDeleted": self.is_deleted,
            "isRenamed": self.is_renamed,
            "renameFrom": self.rename_from,
            "renameTo": self.rename_to,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileAttribution:
        """Deserialize from a dict."""
        return cls(
            file_path=d.get("filePath", ""),
            claude_chars=d.get("claudeChars", 0),
            human_chars=d.get("humanChars", 0),
            claude_lines_added=d.get("claudeLinesAdded", 0),
            human_lines_added=d.get("humanLinesAdded", 0),
            total_lines_added=d.get("totalLinesAdded", 0),
            claude_percent=d.get("claudePercent", 0.0),
            is_new_file=d.get("isNewFile", False),
            is_binary=d.get("isBinary", False),
            is_generated=d.get("isGenerated", False),
            is_deleted=d.get("isDeleted", False),
            is_renamed=d.get("isRenamed", False),
            rename_from=d.get("renameFrom", ""),
            rename_to=d.get("renameTo", ""),
        )


@dataclass
class AttributionState:
    """Per-session tracking state (ported from TS ``AttributionState``).

    *file_states* maps file-path -> ``{"baseline": str, "surface": str}`` where
    ``baseline`` is the hash of the file content at the time Claude first
    touched it and ``surface`` identifies the client surface.
    """

    file_states: dict[str, dict[str, str]] = field(default_factory=dict)
    session_baselines: dict[str, dict[str, str]] = field(default_factory=dict)
    surface: str = field(default_factory=get_client_surface)
    starting_head_sha: str | None = None
    prompt_count: int = 0
    prompt_count_at_last_commit: int = 0
    permission_prompt_count: int = 0
    permission_prompt_count_at_last_commit: int = 0
    escape_count: int = 0
    escape_count_at_last_commit: int = 0
    # Total tokens sent in this session (for cost-tracking integration)
    session_tokens_sent: int = 0
    # Number of tool calls Claude made (for analytics)
    tool_call_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for persistence."""
        return {
            "fileStates": self.file_states,
            "sessionBaselines": self.session_baselines,
            "surface": self.surface,
            "startingHeadSha": self.starting_head_sha,
            "promptCount": self.prompt_count,
            "promptCountAtLastCommit": self.prompt_count_at_last_commit,
            "permissionPromptCount": self.permission_prompt_count,
            "permissionPromptCountAtLastCommit": self.permission_prompt_count_at_last_commit,
            "escapeCount": self.escape_count,
            "escapeCountAtLastCommit": self.escape_count_at_last_commit,
            "sessionTokensSent": self.session_tokens_sent,
            "toolCallCount": self.tool_call_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AttributionState:
        """Deserialize from a dict (used when restoring persisted state)."""
        return cls(
            file_states=d.get("fileStates", {}),
            session_baselines=d.get("sessionBaselines", {}),
            surface=d.get("surface", get_client_surface()),
            starting_head_sha=d.get("startingHeadSha"),
            prompt_count=d.get("promptCount", 0),
            prompt_count_at_last_commit=d.get("promptCountAtLastCommit", 0),
            permission_prompt_count=d.get("permissionPromptCount", 0),
            permission_prompt_count_at_last_commit=d.get("permissionPromptCountAtLastCommit", 0),
            escape_count=d.get("escapeCount", 0),
            escape_count_at_last_commit=d.get("escapeCountAtLastCommit", 0),
            session_tokens_sent=d.get("sessionTokensSent", 0),
            tool_call_count=d.get("toolCallCount", 0),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_empty_attribution_state() -> AttributionState:
    """Create a fresh, empty attribution state."""
    return AttributionState()


def merge_attribution_states(states: list[AttributionState]) -> AttributionState:
    """Merge N session states into one combined state.

    Counters are summed; file_states and session_baselines are shallow-merged
    (later entries win on key conflicts).
    """
    if not states:
        return AttributionState()
    if len(states) == 1:
        return states[0]

    merged = AttributionState(surface=get_client_surface())
    surfaces: set[str] = set()
    for s in states:
        merged.file_states.update(s.file_states)
        merged.session_baselines.update(s.session_baselines)
        merged.prompt_count += s.prompt_count
        merged.prompt_count_at_last_commit += s.prompt_count_at_last_commit
        merged.permission_prompt_count += s.permission_prompt_count
        merged.permission_prompt_count_at_last_commit += (
            s.permission_prompt_count_at_last_commit
        )
        merged.escape_count += s.escape_count
        merged.escape_count_at_last_commit += s.escape_count_at_last_commit
        merged.session_tokens_sent += s.session_tokens_sent
        merged.tool_call_count += s.tool_call_count
        surfaces.add(s.surface)

    # Pick the first non-cli surface as representative, or cli
    for sf in sorted(surfaces):
        if sf != "cli":
            merged.surface = sf
            break
    else:
        merged.surface = "cli"

    # Use the first non-None starting_head_sha
    for s in states:
        if s.starting_head_sha is not None:
            merged.starting_head_sha = s.starting_head_sha
            break

    return merged


# ---------------------------------------------------------------------------
# Counter increment helpers
# ---------------------------------------------------------------------------


def increment_prompt_count(state: AttributionState) -> None:
    """Increment the prompt (conversation turn) counter."""
    if state.prompt_count < 0:  # guard against overflow if somehow negative
        state.prompt_count = 0
    state.prompt_count += 1


def increment_escape_count(state: AttributionState) -> None:
    """Increment the escape (early-abort) counter."""
    if state.escape_count < 0:
        state.escape_count = 0
    state.escape_count += 1


def increment_permission_prompt_count(state: AttributionState) -> None:
    """Increment the permission-prompt counter."""
    if state.permission_prompt_count < 0:
        state.permission_prompt_count = 0
    state.permission_prompt_count += 1


def increment_tool_call_count(state: AttributionState, count: int = 1) -> None:
    """Increment the tool-call counter by *count* (default 1)."""
    if state.tool_call_count < 0:
        state.tool_call_count = 0
    state.tool_call_count += max(count, 0)


def add_session_tokens(state: AttributionState, tokens: int) -> None:
    """Add *tokens* to the session token total."""
    if tokens > 0:
        state.session_tokens_sent += tokens


# ---------------------------------------------------------------------------
# Starting HEAD SHA helpers
# ---------------------------------------------------------------------------


async def get_starting_head_sha(
    cwd: str | None = None,
    timeout: float = 5.0,
) -> str | None:
    """Return the current HEAD commit SHA at session start.

    Returns ``None`` if not in a git repo or the git command fails.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--no-optional-locks",
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or os.getcwd(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            sha = stdout.decode("utf-8").strip()
            return sha if sha else None
        return None
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return None


def get_starting_head_sha_sync(cwd: str | None = None) -> str | None:
    """Synchronous wrapper for `get_starting_head_sha`."""
    return asyncio.run(get_starting_head_sha(cwd))


def update_starting_head_sha(
    state: AttributionState,
    cwd: str | None = None,
) -> str | None:
    """Set *state.starting_head_sha* from the current HEAD and return it.

    Synchronous because it is typically called during session initialization
    when no event loop is active.
    """
    sha = get_starting_head_sha_sync(cwd)
    state.starting_head_sha = sha
    return sha


async def update_starting_head_sha_async(
    state: AttributionState,
    cwd: str | None = None,
) -> str | None:
    """Async version of `update_starting_head_sha` for use inside event loops."""
    sha = await get_starting_head_sha(cwd)
    state.starting_head_sha = sha
    return sha


# ---------------------------------------------------------------------------
# Character-level diff attribution helpers
# ---------------------------------------------------------------------------


def _is_added_line(line: str) -> bool:
    """Check if a diff line is an added line (not a header)."""
    return line.startswith("+") and not line.startswith("+++")


def _is_removed_line(line: str) -> bool:
    """Check if a diff line is a removed line (not a header)."""
    return line.startswith("-") and not line.startswith("---")


def _is_context_line(line: str) -> bool:
    """Check if a diff line is a context line."""
    return line.startswith(" ") or line == ""


def _is_diff_header_line(line: str) -> bool:
    """Check if a line is a diff metadatum header or formatting artifact.

    Also treats empty strings as non-content (trailing newlines in diff output
    produce empty strings after splitting, which are not actual diff content).
    """
    if not line:
        return True
    return line.startswith((
        "diff ", "index ", "---", "+++", "new file", "deleted file",
        "old mode", "new mode", "Binary files", "rename from", "rename to",
        "similarity index",
    )) or line.startswith("@@")


def _count_added_chars(line: str) -> int:
    """Count chars in an added line excluding the leading '+'.

    Returns 0 for context or removal lines.
    """
    if _is_added_line(line):
        return len(line[1:])
    return 0


def _count_removed_chars(line: str) -> int:
    """Count chars in a removed line excluding the leading '-'."""
    if _is_removed_line(line):
        return len(line[1:])
    return 0


def _safe_percent(part: int, total: int) -> float:
    """Return percentage, guarding against division by zero.

    Returns a float rounded to 1 decimal place.
    """
    if total <= 0 or part <= 0:
        return 0.0
    if part >= total:
        return 100.0
    return round((part / total) * 100.0, 1)


def _detect_binary_diff(diff_chunk: str) -> bool:
    """Detect whether a diff chunk represents a binary file change.

    Git indicates binary changes with ``Binary files a/... and b/... differ``
    in the diff header area.
    """
    return bool(re.search(r"^Binary files ", diff_chunk, re.MULTILINE))


def _detect_rename(diff_chunk: str) -> tuple[str, str]:
    """Extract rename from/to paths from a diff chunk.

    Returns ``(rename_from, rename_to)`` — both empty strings if not a rename.
    """
    from_match = re.search(r"^rename from (.+)$", diff_chunk, re.MULTILINE)
    to_match = re.search(r"^rename to (.+)$", diff_chunk, re.MULTILINE)
    if from_match and to_match:
        return from_match.group(1).strip(), to_match.group(1).strip()
    return "", ""


def _detect_deleted_file(diff_chunk: str) -> bool:
    """Detect whether a diff chunk represents a deleted file."""
    return bool(re.search(r"^deleted file mode ", diff_chunk, re.MULTILINE))


# ---------------------------------------------------------------------------
# Compute character-level attribution for a single file's diff
# ---------------------------------------------------------------------------


def compute_char_counts_from_diff(
    diff_text: str,
    file_path: str,
    file_states: dict[str, Any],
) -> FileAttribution:
    """Attribute character-level contributions for a single file.

    Heuristic (aligns with TS behaviour):

    - Entirely **new** files (only '+' lines, no '-' or ' ' lines) are
      attributed 100 % to Claude if the file appears in *file_states*;
      otherwise 100 % to the human.

    - **Deleted** files are attributed 100 % to the human (Claude never
      generates deletions-only commits on its own).

    - **Renamed** files: if Claude touched the new path, the diff on the new
      path is attributed proportionally.

    - **Binary** files: we cannot diff character-by-character; we mark the
      file as binary and attribute based on whether Claude touched it.

    - Modified files are split: added characters are attributed to Claude;
      removed / context characters are attributed to the human.
      If the file is **not** in *file_states* at all, the diff is attributed
      entirely to the human (Claude never touched it).
    """
    result = FileAttribution(file_path=file_path)

    if not diff_text or not diff_text.strip():
        return result

    # ---- Binary detection ----
    if _detect_binary_diff(diff_text):
        result.is_binary = True
        claude_touched = file_path in file_states
        if claude_touched:
            # Cannot count chars — attribute conservatively
            result.claude_chars = 1
            result.claude_lines_added = 1
            result.claude_percent = 100.0
        else:
            result.human_chars = 1
            result.human_lines_added = 1
            result.claude_percent = 0.0
        result.total_lines_added = 1
        return result

    # ---- Rename detection ----
    rename_from, rename_to = _detect_rename(diff_text)
    if rename_from and rename_to:
        result.is_renamed = True
        result.rename_from = rename_from
        result.rename_to = rename_to

    # ---- Deleted file detection ----
    if _detect_deleted_file(diff_text):
        result.is_deleted = True
        lines = diff_text.split("\n")
        for line in lines:
            result.human_chars += _count_removed_chars(line)
        result.total_lines_added = 0
        result.claude_percent = 0.0
        return result

    lines = diff_text.split("\n")

    # Determine if this is effectively a new file (all non-header lines are
    # additions).
    non_header_added = 0
    non_header_total = 0
    for line in lines:
        if _is_diff_header_line(line):
            continue
        non_header_total += 1
        if _is_added_line(line):
            non_header_added += 1

    is_entirely_new = (
        non_header_total > 0 and non_header_added == non_header_total
    )
    result.is_new_file = is_entirely_new

    # Check if file was touched by Claude in any session
    claude_touched = file_path in file_states

    # ---- Entirely new file ----
    if is_entirely_new:
        added_lines = sum(1 for l in lines if _is_added_line(l))
        result.total_lines_added = added_lines
        if claude_touched:
            # 100 % Claude
            for line in lines:
                result.claude_chars += _count_added_chars(line)
            result.claude_lines_added = added_lines
            result.claude_percent = 100.0
        else:
            # 100 % human
            for line in lines:
                result.human_chars += _count_added_chars(line)
            result.human_lines_added = added_lines
            result.claude_percent = 0.0
        return result

    # ---- Mixed / modified file ----
    claude_added_chars = 0
    human_removed_chars = 0
    claude_added_lines = 0
    human_removed_lines = 0

    for line in lines:
        if _is_diff_header_line(line):
            continue
        if _is_added_line(line):
            claude_added_chars += len(line[1:])
            claude_added_lines += 1
        elif _is_removed_line(line):
            human_removed_chars += len(line[1:])
            human_removed_lines += 1

    if claude_touched:
        # Claude touched this file — attribute added lines to Claude,
        # removed lines to human, context chars are neutral (excluded).
        result.claude_chars = claude_added_chars
        result.claude_lines_added = claude_added_lines
        result.human_chars = human_removed_chars
        result.human_lines_added = human_removed_lines
        total = result.claude_chars + result.human_chars
        result.claude_percent = _safe_percent(result.claude_chars, total)
    else:
        # Claude never touched this file — all changes attributed to human.
        result.human_chars = claude_added_chars + human_removed_chars
        result.human_lines_added = claude_added_lines + human_removed_lines
        result.claude_chars = 0
        result.claude_lines_added = 0
        result.claude_percent = 0.0

    result.total_lines_added = result.claude_lines_added + result.human_lines_added
    return result


# Also expose under the alias expected by some call sites
compute_char_counts_from_diff_per_file = compute_char_counts_from_diff


# ---------------------------------------------------------------------------
# Git – fetch diff for specific files
# ---------------------------------------------------------------------------

_GIT_DIFF_TIMEOUT = 10  # seconds


async def _run_git_diff(
    args: list[str],
    cwd: str | None = None,
    timeout: float = _GIT_DIFF_TIMEOUT,
) -> str:
    """Shared inner helper for running a git diff command.

    Returns the decoded stdout on success, or ``""`` on failure.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--no-optional-locks",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or os.getcwd(),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace")
        # Some git diff operations return non-zero for legitimate reasons
        # (e.g. no changes). Do not treat as error; return empty string.
        return ""
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return ""
    except Exception:
        return ""


async def _git_diff_staged(
    files: list[str],
    cwd: str | None = None,
) -> str:
    """Run ``git diff --cached`` for *files* and return unified-diff text.

    If *files* is empty the function returns ``""`` immediately.
    """
    if not files:
        return ""
    return await _run_git_diff(["diff", "--cached", "--", *files], cwd)


async def _git_diff_working_tree(
    files: list[str],
    cwd: str | None = None,
) -> str:
    """Run ``git diff`` (unstaged) for *files*."""
    if not files:
        return ""
    return await _run_git_diff(["diff", "--", *files], cwd)


async def _git_diff_head(
    files: list[str] | None = None,
    cwd: str | None = None,
) -> str:
    """Run ``git diff HEAD`` for optionally-filtered *files*.

    If *files* is None or empty, diffs all tracked changes against HEAD.
    """
    args = ["diff", "HEAD", "--"]
    if files:
        args.extend(files)
    return await _run_git_diff(args, cwd)


# ---------------------------------------------------------------------------
# Diff splitting utilities
# ---------------------------------------------------------------------------


def _split_diff_per_file(diff_text: str) -> dict[str, str]:
    """Split unified-diff output into per-file strings keyed by filename.

    Handles standard ``diff --git`` headers as well as the special
    ``Binary files`` and ``rename`` blocks.
    """
    result: dict[str, str] = {}
    if not diff_text or not diff_text.strip():
        return result

    # Split on "diff --git " headers
    chunks = re.split(r"^(?=diff --git )", diff_text, flags=re.MULTILINE)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.search(r"^diff --git a/(.+?) b/(.+?)$", chunk, re.MULTILINE)
        if m:
            # Prefer the "new" file path (b/), fall back to "a/"
            file_path = m.group(2).strip() or m.group(1).strip()
            if file_path:
                result[file_path] = chunk
                continue
        # Handle binary-only chunks that might not match the standard header
        bin_m = re.search(
            r"^Binary files a/(.+?) and b/(.+?) differ",
            chunk,
            re.MULTILINE,
        )
        if bin_m:
            file_path = bin_m.group(2).strip() or bin_m.group(1).strip()
            result[file_path] = chunk
    return result


# ---------------------------------------------------------------------------
# Generated-file detection (mirrors generated_files.is_generated_file logic)
# ---------------------------------------------------------------------------


def _is_generated(file_path: str) -> bool:
    """Quick inline generated-file check (delegates to generated_files module).

    Falls back to local pattern matching if the generated_files module is
    unavailable.
    """
    try:
        from hare.utils.generated_files import is_generated_file

        return is_generated_file(file_path)
    except ImportError:
        # Fallback: check common patterns
        from pathlib import PurePosixPath

        name = os.path.basename(file_path).lower()
        posix_path = file_path.replace(os.sep, "/")
        normalized_path = "/" + posix_path.lstrip("/")

        # Lock files / generated extensions
        excluded_names = frozenset((
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "bun.lockb", "bun.lock", "composer.lock", "gemfile.lock",
            "cargo.lock", "poetry.lock", "pipfile.lock",
            "shrinkwrap.json", "npm-shrinkwrap.json",
        ))
        if name in excluded_names:
            return True

        ext = PurePosixPath(file_path).suffix.lower()
        excluded_exts = frozenset((
            ".lock", ".min.js", ".min.css", ".min.html",
            ".bundle.js", ".bundle.css", ".generated.ts", ".generated.js",
            ".d.ts",
        ))
        if ext in excluded_exts:
            return True
        # Compound extensions (e.g., .min.js)
        parts = name.split(".")
        if len(parts) > 2:
            compound = "." + ".".join(parts[-2:])
            if compound.lower() in excluded_exts:
                return True

        gen_patterns = (
            re.compile(r"^.*\.min\.[a-z]+$", re.I),
            re.compile(r"^.*-min\.[a-z]+$", re.I),
            re.compile(r"^.*\.bundle\.[a-z]+$", re.I),
            re.compile(r"^.*\.generated\.[a-z]+$", re.I),
            re.compile(r"^.*\.gen\.[a-z]+$", re.I),
            re.compile(r"^.*\.auto\.[a-z]+$", re.I),
            re.compile(r"^.*_generated\.[a-z]+$", re.I),
            re.compile(r"^.*_gen\.[a-z]+$", re.I),
            re.compile(r"^.*\.pb\.(go|js|ts|py|rb)$", re.I),
            re.compile(r"^.*_pb2?\.py$", re.I),
            re.compile(r"^.*\.pb\.h$", re.I),
            re.compile(r"^.*\.grpc\.[a-z]+$", re.I),
            re.compile(r"^.*\.swagger\.[a-z]+$", re.I),
            re.compile(r"^.*\.openapi\.[a-z]+$", re.I),
        )
        for pat in gen_patterns:
            if pat.match(name):
                return True

        gen_dirs = (
            "/dist/", "/build/", "/out/", "/output/",
            "/node_modules/", "/vendor/", "/vendored/",
            "/third_party/", "/third-party/", "/external/",
            "/.next/", "/.nuxt/", "/.svelte-kit/",
            "/coverage/", "/__pycache__/", "/.tox/",
            "/venv/", "/.venv/", "/target/release/",
            "/target/debug/",
        )
        for d in gen_dirs:
            if d in normalized_path:
                return True

        return False


# ---------------------------------------------------------------------------
# Attribution footer generation
# ---------------------------------------------------------------------------


def _check_undercover() -> bool:
    """Check whether undercover mode is active (silent on import errors)."""
    try:
        from hare.utils.undercover import is_undercover

        return is_undercover()
    except ImportError:
        return False


def generate_attribution_footer(
    claude_percent: float,
    claude_chars: int,
    human_chars: int,
    min_percent: int = _DEFAULT_MIN_CLAUDE_PERCENT,
    min_chars: int = _DEFAULT_MIN_CLAUDE_CHARS,
    force: bool = False,
) -> str:
    """Return the Co-Authored-By footer for a qualifying commit.

    The footer is appended to the commit message. It is generated only when
    *claude_percent* >= *min_percent* AND *claude_chars* >= *min_chars*
    (or *force* is True).

    In undercover mode the footer is always empty.
    """
    if _check_undercover():
        return ""

    if force:
        return "\n\nCo-Authored-By: Claude Code <noreply@anthropic.com>"

    if claude_percent < min_percent:
        return ""
    if claude_chars < min_chars:
        return ""
    # Both thresholds satisfied
    return "\n\nCo-Authored-By: Claude Code <noreply@anthropic.com>"


def generate_pr_footer(
    claude_percent: float,
    claude_chars: int,
    human_chars: int,
    min_percent: int = _DEFAULT_MIN_CLAUDE_PERCENT,
    min_chars: int = _DEFAULT_MIN_CLAUDE_CHARS,
    force: bool = False,
) -> str:
    """Return the PR attribution footer (emoji + link).

    Only generated when attribution thresholds are met, unless *force* is True.
    In undercover mode this returns ``""``.
    """
    if _check_undercover():
        return ""
    if force:
        return "\n\n\xf0\x9f\xa4\x96 Generated with [Claude Code](https://claude.com/claude-code)"
    if claude_percent < min_percent:
        return ""
    if claude_chars < min_chars:
        return ""
    return "\n\n\xf0\x9f\xa4\x96 Generated with [Claude Code](https://claude.com/claude-code)"


def get_attribution_texts(
    min_percent: int = _DEFAULT_MIN_CLAUDE_PERCENT,
    min_chars: int = _DEFAULT_MIN_CLAUDE_CHARS,
) -> dict[str, str]:
    """Return the dictionary of attribution footers used by commit commands.

    Mirrors `get_attribution_texts()` in `commit.py` and `commit_push_pr.py`.

    Keys:
      ``"commit"`` — footer for git commit messages.
      ``"pr"``     — footer for PR descriptions.

    If undercover mode is active, both values are ``""``.
    """
    if _check_undercover():
        return {"commit": "", "pr": ""}
    commit_footer = generate_attribution_footer(
        100.0, _DEFAULT_MIN_CLAUDE_CHARS, 0, min_percent, min_chars, force=True,
    )
    pr_footer = generate_pr_footer(
        100.0, _DEFAULT_MIN_CLAUDE_CHARS, 0, min_percent, min_chars, force=True,
    )
    return {"commit": commit_footer, "pr": pr_footer}


# ---------------------------------------------------------------------------
# Main: calculate_commit_attribution
# ---------------------------------------------------------------------------


async def calculate_commit_attribution(
    states: list[AttributionState],
    staged_files: list[str],
    cwd: str | None = None,
    include_unstaged: bool = True,
    min_percent: int = _DEFAULT_MIN_CLAUDE_PERCENT,
    min_chars: int = _DEFAULT_MIN_CLAUDE_CHARS,
) -> dict[str, Any]:
    """Compute full commit-attribution from session states and staged files.

    Parameters
    ----------
    states:
        Per-session tracking states.
    staged_files:
        Paths of files that are staged for commit.
    cwd:
        Working directory (defaults to current working directory).
    include_unstaged:
        If True, also include unstaged changes in the diff analysis.
    min_percent:
        Minimum Claude contribution percentage for attribution.
    min_chars:
        Minimum Claude character contribution for attribution.

    Returns
    -------
    dict with keys:

    ``version``
        Always ``1``.
    ``summary``
        Top-level counts: ``claudePercent``, ``claudeChars``, ``humanChars``,
        ``surfaces``, ``totalFiles``, ``attributionFooter``.
    ``files``
        Dict of file_path -> `FileAttribution`-like dict.
    ``surfaceBreakdown``
        Per-surface char counts.
    ``excludedGenerated``
        List of generated/vendored file paths that were excluded from counting.
    ``sessions``
        List of per-session summaries.
    ``qualifiesForAttribution``
        Whether the commit should get a Co-Authored-By footer.
    ``attributionFooter``
        Footer string (empty if not qualifying).
    """
    effective_cwd = cwd or os.getcwd()

    # 1. Merge states
    merged = merge_attribution_states(states) if states else AttributionState()
    file_states = merged.file_states

    # 2. Fetch diffs (staged + optionally unstaged)
    staged_diff = await _git_diff_staged(staged_files, effective_cwd)
    combined_diff = staged_diff
    if include_unstaged:
        unstaged_diff = await _git_diff_working_tree(staged_files, effective_cwd)
        if unstaged_diff:
            combined_diff = staged_diff + "\n" + unstaged_diff

    per_file_diffs = _split_diff_per_file(combined_diff)

    # If no staged files given or diff was empty, try HEAD-based fallback
    if not per_file_diffs:
        head_diff = await _git_diff_head(
            files=staged_files if staged_files else None,
            cwd=effective_cwd,
        )
        if head_diff:
            per_file_diffs = _split_diff_per_file(head_diff)

    # 3. Compute per-file attribution
    file_attributions: dict[str, FileAttribution] = {}
    excluded_generated: list[str] = []
    total_claude_chars = 0
    total_human_chars = 0
    surfaces: set[str] = {s.surface for s in states} if states else {"cli"}

    for file_path, diff_chunk in per_file_diffs.items():
        fa = compute_char_counts_from_diff(diff_chunk, file_path, file_states)

        # Mark generated/vendored files for exclusion
        if _is_generated(file_path):
            fa.is_generated = True
            excluded_generated.append(file_path)
            file_attributions[file_path] = fa
            continue

        file_attributions[file_path] = fa
        total_claude_chars += fa.claude_chars
        total_human_chars += fa.human_chars

    # 4. Surface breakdown
    surface_breakdown: dict[str, dict[str, int]] = {}
    for sf in surfaces:
        surface_breakdown[sf] = {"claudeChars": 0, "humanChars": 0}

    for fp, fa in file_attributions.items():
        if fa.is_generated:
            continue  # excluded from surface breakdown
        # Attribute to the surface recorded in file_states (defaults to merged.surface)
        fstate = file_states.get(fp, {})
        sf = (
            fstate.get("surface", merged.surface)
            if isinstance(fstate, dict)
            else merged.surface
        )
        if sf not in surface_breakdown:
            surface_breakdown[sf] = {"claudeChars": 0, "humanChars": 0}
        surface_breakdown[sf]["claudeChars"] += fa.claude_chars
        surface_breakdown[sf]["humanChars"] += fa.human_chars

    total_chars = total_claude_chars + total_human_chars
    claude_percent = _safe_percent(total_claude_chars, total_chars)

    # 5. Session summaries
    session_summaries: list[dict[str, Any]] = []
    for idx, state in enumerate(states):
        session_summaries.append({
            "surface": state.surface,
            "promptCount": state.prompt_count,
            "escapeCount": state.escape_count,
            "permissionPromptCount": state.permission_prompt_count,
            "filesTouched": len(state.file_states),
            "sessionTokensSent": state.session_tokens_sent,
            "toolCallCount": state.tool_call_count,
            "sinceLastCommit": {
                "prompts": state.prompt_count - state.prompt_count_at_last_commit,
                "escapes": state.escape_count - state.escape_count_at_last_commit,
                "permissionPrompts": (
                    state.permission_prompt_count
                    - state.permission_prompt_count_at_last_commit
                ),
            },
        })

    # 6. Attribution footer decision
    qualifies = (
        claude_percent >= min_percent
        and total_claude_chars >= min_chars
    )
    footer = generate_attribution_footer(
        claude_percent, total_claude_chars, total_human_chars,
        min_percent=min_percent, min_chars=min_chars,
    )

    return {
        "version": 1,
        "summary": {
            "claudePercent": claude_percent,
            "claudeChars": total_claude_chars,
            "humanChars": total_human_chars,
            "surfaces": sorted(surfaces),
            "totalFiles": len(file_attributions),
            "attributionFooter": footer,
        },
        "files": {
            fp: fa.to_dict()
            for fp, fa in file_attributions.items()
        },
        "surfaceBreakdown": surface_breakdown,
        "excludedGenerated": excluded_generated,
        "sessions": session_summaries,
        "qualifiesForAttribution": qualifies,
        "attributionFooter": footer,
    }


# ---------------------------------------------------------------------------
# Convenience: compute attribution synchronously (for tests / scripts)
# ---------------------------------------------------------------------------


def calculate_commit_attribution_sync(
    states: list[AttributionState],
    staged_files: list[str],
    cwd: str | None = None,
    include_unstaged: bool = True,
    min_percent: int = _DEFAULT_MIN_CLAUDE_PERCENT,
    min_chars: int = _DEFAULT_MIN_CLAUDE_CHARS,
) -> dict[str, Any]:
    """Synchronous wrapper around `calculate_commit_attribution`.

    Uses `asyncio.run()` — not safe inside an existing event loop.
    """
    return asyncio.run(
        calculate_commit_attribution(
            states, staged_files, cwd,
            include_unstaged=include_unstaged,
            min_percent=min_percent,
            min_chars=min_chars,
        )
    )


# ---------------------------------------------------------------------------
# Mark-reset helpers (called at commit time to snapshot counters)
# ---------------------------------------------------------------------------


def mark_commit_counters(state: AttributionState) -> None:
    """Snapshot current counters so 'since last commit' deltas are correct.

    Call this at the moment a commit is created so the next commit can
    report accurate per-commit metrics.
    """
    state.prompt_count_at_last_commit = state.prompt_count
    state.permission_prompt_count_at_last_commit = state.permission_prompt_count
    state.escape_count_at_last_commit = state.escape_count


def reset_after_commit(state: AttributionState) -> None:
    """Full reset after a commit is created.

    In addition to snapshotting counters (same as `mark_commit_counters`),
    clears the per-file touch records so subsequent changes are tracked
    against a fresh baseline.
    """
    mark_commit_counters(state)
    # Clear file-level touch tracking so a new baseline starts
    state.file_states.clear()
    state.session_baselines.clear()


# ---------------------------------------------------------------------------
# File-touch and baseline recording
# ---------------------------------------------------------------------------


def record_file_touch(
    state: AttributionState,
    file_path: str,
    content_hash: str | None = None,
    surface: str | None = None,
) -> None:
    """Record that Claude touched *file_path* in this session.

    Stores the content-hash baseline and surface so later attribution can
    determine whether the file's diff should be credited to Claude.

    If *content_hash* is not provided and the file exists on disk, we read
    it and compute the hash inline.
    """
    if content_hash is None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                content_hash = compute_content_hash(fh.read())
        except (OSError, UnicodeDecodeError):
            # Binary or unreadable — try binary hash
            try:
                with open(file_path, "rb") as fh:
                    content_hash = compute_content_hash_bytes(fh.read())
            except OSError:
                content_hash = ""
    state.file_states[file_path] = {
        "baseline": content_hash or "",
        "surface": surface or state.surface,
    }


def record_file_touch_simple(
    state: AttributionState,
    file_path: str,
) -> None:
    """Record a file touch without reading content (lighter weight).

    Used when the content hash is not yet available but we need to mark
    the file as affected by Claude for subsequent attribution.
    """
    state.file_states[file_path] = {
        "baseline": "",
        "surface": state.surface,
    }


def record_baseline(
    state: AttributionState,
    file_path: str,
    content: str,
    surface: str | None = None,
) -> None:
    """Record a file baseline (hash of current content) before modifications."""
    h = compute_content_hash(content)
    state.session_baselines[file_path] = {
        "hash": h,
        "surface": surface or state.surface,
    }


def get_file_state(
    state: AttributionState,
    file_path: str,
) -> dict[str, str] | None:
    """Look up the recorded state for *file_path*.

    Returns ``None`` if the file has not been recorded.
    """
    return state.file_states.get(file_path)


def get_file_baseline(
    state: AttributionState,
    file_path: str,
) -> str | None:
    """Return the content-hash baseline for *file_path* if recorded.

    Checks ``file_states`` first (touch records keyed by ``"baseline"``),
    then ``session_baselines`` (pre-edit snapshots keyed by ``"hash"``).

    Returns ``None`` if the file has no recorded baseline.
    """
    # Check file_states first (touch records)
    fs = state.file_states.get(file_path)
    if fs and isinstance(fs, dict):
        baseline = fs.get("baseline")
        if baseline:
            return baseline
    # Fall back to session_baselines (pre-edit snapshots)
    sb = state.session_baselines.get(file_path)
    if sb and isinstance(sb, dict):
        return sb.get("hash")
    return None


def has_file_changed_since_baseline(
    state: AttributionState,
    file_path: str,
) -> bool:
    """Check whether *file_path* has changed since its recorded baseline.

    Returns True if the file content no longer matches the stored hash,
    or if there is no baseline recorded at all.
    """
    baseline = get_file_baseline(state, file_path)
    if baseline is None:
        return True
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            current_hash = compute_content_hash(fh.read())
        return current_hash != baseline
    except (OSError, UnicodeDecodeError):
        try:
            with open(file_path, "rb") as fh:
                current_hash = compute_content_hash_bytes(fh.read())
            return current_hash != baseline
        except OSError:
            return True


# ---------------------------------------------------------------------------
# Attribution qualifiers
# ---------------------------------------------------------------------------


def is_commit_qualified(
    claude_chars: int,
    human_chars: int,
    min_percent: int = _DEFAULT_MIN_CLAUDE_PERCENT,
    min_chars: int = _DEFAULT_MIN_CLAUDE_CHARS,
) -> bool:
    """Return True if Claude's contribution warrants attribution.

    Requires both the percentage AND absolute char thresholds to be met.
    """
    total = claude_chars + human_chars
    if total <= 0:
        return False
    if claude_chars < min_chars:
        return False
    return (claude_chars / total) * 100.0 >= min_percent


# ---------------------------------------------------------------------------
# State persistence helpers
# ---------------------------------------------------------------------------


def serialize_attribution_state(state: AttributionState) -> dict[str, Any]:
    """Serialize an AttributionState to a JSON-compatible dict."""
    return state.to_dict()


def deserialize_attribution_state(data: dict[str, Any]) -> AttributionState:
    """Reconstruct an AttributionState from a dict (e.g. loaded from JSON)."""
    return AttributionState.from_dict(data)


def serialize_file_attribution(fa: FileAttribution) -> dict[str, Any]:
    """Serialize a FileAttribution to a JSON-compatible dict."""
    return fa.to_dict()


def deserialize_file_attribution(data: dict[str, Any]) -> FileAttribution:
    """Reconstruct a FileAttribution from a dict."""
    return FileAttribution.from_dict(data)


# ---------------------------------------------------------------------------
# Utility: build an attribution summary for a single session
# ---------------------------------------------------------------------------


def compute_session_summary(state: AttributionState) -> dict[str, Any]:
    """Return a compact summary of *state* suitable for inline display."""
    files_touched = len(state.file_states)
    baselines_set = len(state.session_baselines)
    return {
        "surface": state.surface,
        "prompts": state.prompt_count,
        "promptsSinceCommit": (
            state.prompt_count - state.prompt_count_at_last_commit
        ),
        "escapes": state.escape_count,
        "escapesSinceCommit": (
            state.escape_count - state.escape_count_at_last_commit
        ),
        "permissionPrompts": state.permission_prompt_count,
        "permissionPromptsSinceCommit": (
            state.permission_prompt_count
            - state.permission_prompt_count_at_last_commit
        ),
        "filesTouched": files_touched,
        "baselinesSet": baselines_set,
        "startingHeadSha": state.starting_head_sha,
        "toolCalls": state.tool_call_count,
        "tokensSent": state.session_tokens_sent,
    }
