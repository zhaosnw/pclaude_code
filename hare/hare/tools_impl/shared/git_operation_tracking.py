"""Port of: src/tools/shared/gitOperationTracking.ts

Detects git operations from command strings using regex patterns.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# Git commit regex — matches: git commit -m "..." or git commit --amend etc.
_COMMIT_RE = re.compile(r"\bgit\s+commit\b", re.IGNORECASE)

# Git push regex
_PUSH_RE = re.compile(r"\bgit\s+push\b", re.IGNORECASE)

# Git destructive operations
_DESTRUCTIVE_RE = re.compile(
    r"\bgit\s+(reset\s+--hard|push\s+--force|push\s+-f|checkout\s+--force|clean\s+-f)"
    r"|git\s+(branch\s+-D|stash\s+drop|rebase\s+--abort|rebase\s+--skip)",
    re.IGNORECASE,
)

# Git cherry-pick
_CHERRYPICK_RE = re.compile(r"\bgit\s+cherry-pick\b", re.IGNORECASE)

# Git merge
_MERGE_RE = re.compile(r"\bgit\s+merge\b", re.IGNORECASE)

# Git rebase
_REBASE_RE = re.compile(r"\bgit\s+rebase\b", re.IGNORECASE)

# PR creation via gh CLI
_GH_PR_RE = re.compile(r"\bgh\s+pr\s+create\b", re.IGNORECASE)

# PR creation via glab CLI (GitLab)
_GLAB_MR_RE = re.compile(r"\bglab\s+mr\s+create\b", re.IGNORECASE)

# PR creation via curl to GitHub/GitLab API
_CURL_PR_RE = re.compile(
    r"\bcurl\b.*\b(pulls|merge_requests)\b", re.IGNORECASE | re.DOTALL
)

# PR URL detection from output
_PR_URL_RE = re.compile(
    r"https?://github\.com/[\w.-]+/[\w.-]+/pull/\d+"
    r"|https?://gitlab\.\w+/[\w.-]+/[\w.-]+/-/merge_requests/\d+",
    re.IGNORECASE,
)

# Commit SHA extraction
_COMMIT_SHA_RE = re.compile(r"\b([0-9a-f]{40})\b", re.IGNORECASE)


@dataclass
class GitOperation:
    command: str
    timestamp: float = 0.0
    exit_code: int = 0
    operation_type: str = ""
    commit_sha: str | None = None
    pr_url: str | None = None


class GitOperationTracker:
    """Tracks git operations from shell command strings.

    Detects commits, pushes, destructive operations, cherry-picks,
    merges, rebases, and PR creations from command text.
    """

    def __init__(self) -> None:
        self._ops: list[GitOperation] = []

    def record(
        self, command: str, exit_code: int = 0, output: str = ""
    ) -> GitOperation:
        op_type = self._detect_type(command)
        commit_sha = self._extract_commit_sha(output) if output else None
        pr_url = self._extract_pr_url(output) if output else None

        op = GitOperation(
            command=command,
            timestamp=time.time(),
            exit_code=exit_code,
            operation_type=op_type,
            commit_sha=commit_sha,
            pr_url=pr_url,
        )
        self._ops.append(op)
        return op

    def _detect_type(self, command: str) -> str:
        if _GH_PR_RE.search(command) or _GLAB_MR_RE.search(command):
            return "pr_create"
        if _DESTRUCTIVE_RE.search(command):
            return "destructive"
        if _COMMIT_RE.search(command):
            return "commit"
        if _PUSH_RE.search(command):
            return "push"
        if _CHERRYPICK_RE.search(command):
            return "cherry_pick"
        if _MERGE_RE.search(command):
            return "merge"
        if _REBASE_RE.search(command):
            return "rebase"
        if _CURL_PR_RE.search(command):
            return "pr_create"
        return "git"

    def _extract_commit_sha(self, output: str) -> str | None:
        m = _COMMIT_SHA_RE.search(output)
        return m.group(1) if m else None

    def _extract_pr_url(self, output: str) -> str | None:
        m = _PR_URL_RE.search(output)
        return m.group(0) if m else None

    def get_recent(self, limit: int = 20) -> list[GitOperation]:
        return self._ops[-limit:]

    def get_commits(self, limit: int = 10) -> list[GitOperation]:
        return [op for op in self._ops if op.operation_type == "commit"][-limit:]

    def get_pr_creations(self, limit: int = 5) -> list[GitOperation]:
        return [op for op in self._ops if op.operation_type == "pr_create"][-limit:]

    def has_destructive_ops(self) -> bool:
        return any(op.operation_type == "destructive" for op in self._ops)

    def clear(self) -> None:
        self._ops.clear()
