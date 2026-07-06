"""
Git diff stats, numstat/shortstat parsing, and single-file PR-style diffs.

Faithful port of: recovered-from-cli-js-map/src/utils/gitDiff.ts
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from hare.utils.cwd import get_cwd
from hare.utils.exec_file_no_throw import (
    exec_file_no_throw,
    exec_file_no_throw_with_cwd,
)
from hare.utils.git_utils import (
    find_git_root,
    get_default_branch,
    get_git_dir,
    get_is_git,
    git_exe,
)

GIT_TIMEOUT_MS = 5000
MAX_FILES = 50
MAX_DIFF_SIZE_BYTES = 1_000_000
MAX_LINES_PER_FILE = 400
MAX_FILES_FOR_DETAILS = 500
SINGLE_FILE_DIFF_TIMEOUT_MS = 3000


@dataclass
class GitDiffStats:
    files_count: int = 0
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class PerFileStats:
    added: int = 0
    removed: int = 0
    is_binary: bool = False
    is_untracked: bool = False


@dataclass
class StructuredPatchHunk:
    old_start: int = 0
    old_lines: int = 0
    new_start: int = 0
    new_lines: int = 0
    lines: list[str] = field(default_factory=list)


@dataclass
class GitDiffResult:
    stats: GitDiffStats = field(default_factory=GitDiffStats)
    per_file_stats: dict[str, PerFileStats] = field(default_factory=dict)
    hunks: dict[str, list[StructuredPatchHunk]] = field(default_factory=dict)


@dataclass
class NumstatResult:
    stats: GitDiffStats = field(default_factory=GitDiffStats)
    per_file_stats: dict[str, PerFileStats] = field(default_factory=dict)


@dataclass
class ToolUseDiff:
    filename: str = ""
    status: Literal["modified", "added"] = "modified"
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: str = ""
    repository: Optional[str] = None


def get_cached_repository() -> str | None:
    """Stub for detectRepository.getCachedRepository — returns owner/repo when wired."""
    return None


def is_file_within_read_size_limit(path: str, max_bytes: int) -> bool:
    """Mirrors file.ts isFileWithinReadSizeLimit for diff sizing."""
    try:
        return os.path.getsize(path) <= max_bytes
    except OSError:
        return False


async def fetch_git_diff() -> GitDiffResult | None:
    if not await get_is_git():
        return None
    if await is_in_transient_git_state():
        return None

    shortstat_out, shortstat_code = await _exec_git(
        ["--no-optional-locks", "diff", "HEAD", "--shortstat"],
    )
    if shortstat_code == 0:
        quick = parse_shortstat(shortstat_out)
        if quick and quick.files_count > MAX_FILES_FOR_DETAILS:
            return GitDiffResult(stats=quick, per_file_stats={}, hunks={})

    numstat_out, numstat_code = await _exec_git(
        ["--no-optional-locks", "diff", "HEAD", "--numstat"],
    )
    if numstat_code != 0:
        return None

    num = parse_git_numstat(numstat_out)
    remaining = MAX_FILES - len(num.per_file_stats)
    if remaining > 0:
        untracked = await _fetch_untracked_files(remaining)
        if untracked:
            num.stats.files_count += len(untracked)
            for k, v in untracked.items():
                num.per_file_stats[k] = v

    return GitDiffResult(stats=num.stats, per_file_stats=num.per_file_stats, hunks={})


async def fetch_git_diff_hunks() -> dict[str, list[StructuredPatchHunk]]:
    if not await get_is_git():
        return {}
    if await is_in_transient_git_state():
        return {}

    diff_out, diff_code = await _exec_git(
        ["--no-optional-locks", "diff", "HEAD"],
    )
    if diff_code != 0:
        return {}
    return parse_git_diff(diff_out)


async def _exec_git(args: list[str]) -> tuple[str, int]:
    r = await exec_file_no_throw(
        git_exe(),
        args,
        {"timeout": GIT_TIMEOUT_MS, "preserve_output_on_error": False},
    )
    return (r.get("stdout") or ""), int(r.get("code") or 1)


def parse_git_numstat(stdout: str) -> NumstatResult:
    lines = [ln for ln in stdout.strip().split("\n") if ln]
    added = 0
    removed = 0
    valid_file_count = 0
    per_file: dict[str, PerFileStats] = {}

    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        valid_file_count += 1
        add_str, rem_str = parts[0], parts[1]
        file_path = "\t".join(parts[2:])
        is_binary = add_str == "-" or rem_str == "-"
        file_added = 0 if is_binary else int(add_str or "0")
        file_removed = 0 if is_binary else int(rem_str or "0")
        added += file_added
        removed += file_removed
        if len(per_file) < MAX_FILES:
            per_file[file_path] = PerFileStats(
                added=file_added,
                removed=file_removed,
                is_binary=is_binary,
            )

    return NumstatResult(
        stats=GitDiffStats(
            files_count=valid_file_count,
            lines_added=added,
            lines_removed=removed,
        ),
        per_file_stats=per_file,
    )


def parse_git_diff(stdout: str) -> dict[str, list[StructuredPatchHunk]]:
    result: dict[str, list[StructuredPatchHunk]] = {}
    if not stdout.strip():
        return result

    file_diffs = re.split(r"^diff --git ", stdout, flags=re.MULTILINE)
    for raw in file_diffs:
        if not raw:
            continue
        if len(result) >= MAX_FILES:
            break
        file_diff = raw
        if len(file_diff) > MAX_DIFF_SIZE_BYTES:
            continue

        lines = file_diff.split("\n")
        header = re.match(r"^a/(.+?) b/(.+)$", lines[0] if lines else "")
        if not header:
            continue
        file_path = header.group(2) or header.group(1) or ""

        file_hunks: list[StructuredPatchHunk] = []
        current: StructuredPatchHunk | None = None
        line_count = 0

        for i in range(1, len(lines)):
            line = lines[i]
            hunk_m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if hunk_m:
                if current:
                    file_hunks.append(current)
                current = StructuredPatchHunk(
                    old_start=int(hunk_m.group(1) or 0),
                    old_lines=int(hunk_m.group(2) or 1),
                    new_start=int(hunk_m.group(3) or 0),
                    new_lines=int(hunk_m.group(4) or 1),
                )
                continue

            if line.startswith(
                (
                    "index ",
                    "---",
                    "+++",
                    "new file",
                    "deleted file",
                    "old mode",
                    "new mode",
                    "Binary files",
                )
            ):
                continue

            if current and (
                line.startswith("+")
                or line.startswith("-")
                or line.startswith(" ")
                or line == ""
            ):
                if line_count >= MAX_LINES_PER_FILE:
                    continue
                current.lines.append(str(line))
                line_count += 1

        if current:
            file_hunks.append(current)
        if file_hunks:
            result[file_path] = file_hunks

    return result


async def is_in_transient_git_state() -> bool:
    git_dir = await get_git_dir(get_cwd())
    if not git_dir:
        return False
    for name in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        p = os.path.join(git_dir, name)
        try:
            if os.access(p, os.F_OK):
                return True
        except OSError:
            continue
    return False


async def _fetch_untracked_files(max_files: int) -> dict[str, PerFileStats] | None:
    r = await exec_file_no_throw(
        git_exe(),
        ["--no-optional-locks", "ls-files", "--others", "--exclude-standard"],
        {"timeout": GIT_TIMEOUT_MS, "preserve_output_on_error": False},
    )
    if r.get("code") != 0 or not (r.get("stdout") or "").strip():
        return None
    paths = [p for p in (r.get("stdout") or "").strip().split("\n") if p]
    if not paths:
        return None
    out: dict[str, PerFileStats] = {}
    for p in paths[:max_files]:
        out[p] = PerFileStats(
            added=0,
            removed=0,
            is_binary=False,
            is_untracked=True,
        )
    return out


def parse_shortstat(stdout: str) -> GitDiffStats | None:
    m = re.search(
        r"(\d+)\s+files?\s+changed"
        r"(?:,\s+(\d+)\s+insertions?\(\+\))?"
        r"(?:,\s+(\d+)\s+deletions?\(-\))?",
        stdout,
    )
    if not m:
        return None
    return GitDiffStats(
        files_count=int(m.group(1) or 0),
        lines_added=int(m.group(2) or 0),
        lines_removed=int(m.group(3) or 0),
    )


def _parse_raw_diff_to_tool_use_diff(
    filename: str,
    raw_diff: str,
    status: Literal["modified", "added"],
) -> ToolUseDiff:
    lines = raw_diff.split("\n")
    patch_lines: list[str] = []
    in_hunks = False
    additions = 0
    deletions = 0

    for line in lines:
        if line.startswith("@@"):
            in_hunks = True
        if in_hunks:
            patch_lines.append(line)
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

    return ToolUseDiff(
        filename=filename,
        status=status,
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
        patch="\n".join(patch_lines),
    )


async def _get_diff_ref(git_root: str) -> str:
    base_branch = os.environ.get("CLAUDE_CODE_BASE_REF") or await get_default_branch()
    r = await exec_file_no_throw_with_cwd(
        git_exe(),
        ["--no-optional-locks", "merge-base", "HEAD", base_branch],
        cwd=git_root,
        timeout=SINGLE_FILE_DIFF_TIMEOUT_MS,
        preserve_output_on_error=False,
    )
    if r.get("code") == 0 and (r.get("stdout") or "").strip():
        return (r.get("stdout") or "").strip()
    return "HEAD"


async def _generate_synthetic_diff(
    git_path: str,
    absolute_file_path: str,
) -> ToolUseDiff | None:
    try:
        if not is_file_within_read_size_limit(absolute_file_path, MAX_DIFF_SIZE_BYTES):
            return None
        with open(absolute_file_path, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        lines = content.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        line_count = len(lines)
        added_lines = "\n".join(f"+{ln}" for ln in lines)
        patch = f"@@ -0,0 +1,{line_count} @@\n{added_lines}"
        return ToolUseDiff(
            filename=git_path,
            status="added",
            additions=line_count,
            deletions=0,
            changes=line_count,
            patch=patch,
        )
    except OSError:
        return None


async def fetch_single_file_git_diff(absolute_file_path: str) -> ToolUseDiff | None:
    git_root = find_git_root(os.path.dirname(absolute_file_path))
    if not git_root:
        return None

    git_path = os.path.relpath(absolute_file_path, git_root).replace(os.sep, "/")
    repository = get_cached_repository()

    ls_r = await exec_file_no_throw_with_cwd(
        git_exe(),
        ["--no-optional-locks", "ls-files", "--error-unmatch", git_path],
        cwd=git_root,
        timeout=SINGLE_FILE_DIFF_TIMEOUT_MS,
        preserve_output_on_error=False,
    )
    if ls_r.get("code") == 0:
        diff_ref = await _get_diff_ref(git_root)
        diff_r = await exec_file_no_throw_with_cwd(
            git_exe(),
            ["--no-optional-locks", "diff", diff_ref, "--", git_path],
            cwd=git_root,
            timeout=SINGLE_FILE_DIFF_TIMEOUT_MS,
            preserve_output_on_error=False,
        )
        if diff_r.get("code") != 0:
            return None
        stdout = diff_r.get("stdout") or ""
        if not stdout:
            return None
        base = _parse_raw_diff_to_tool_use_diff(git_path, stdout, "modified")
        base.repository = repository
        return base

    synthetic = await _generate_synthetic_diff(git_path, absolute_file_path)
    if not synthetic:
        return None
    synthetic.repository = repository
    return synthetic


__all__ = [
    "GIT_TIMEOUT_MS",
    "MAX_DIFF_SIZE_BYTES",
    "MAX_FILES",
    "MAX_FILES_FOR_DETAILS",
    "MAX_LINES_PER_FILE",
    "GitDiffResult",
    "GitDiffStats",
    "NumstatResult",
    "PerFileStats",
    "StructuredPatchHunk",
    "ToolUseDiff",
    "fetch_git_diff",
    "fetch_git_diff_hunks",
    "fetch_single_file_git_diff",
    "get_cached_repository",
    "is_file_within_read_size_limit",
    "is_in_transient_git_state",
    "parse_git_diff",
    "parse_git_numstat",
    "parse_shortstat",
]
