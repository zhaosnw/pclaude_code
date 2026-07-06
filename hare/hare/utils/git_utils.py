"""
Git repository helpers: roots, remotes, state, issue preservation.

Faithful port of: recovered-from-cli-js-map/src/utils/git.ts

External filesystem caches from gitFilesystem.ts are inlined or stubbed via git subprocess.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

from hare.constants.files import has_binary_extension, is_binary_content
from hare.utils.cwd import get_cwd
from hare.utils.debug import log_for_debugging
from hare.utils.exec_file_no_throw import (
    exec_file_no_throw,
    exec_file_no_throw_with_cwd,
)
from hare.utils.log import log_error

if TYPE_CHECKING:
    pass

_GIT_ROOT_NOT_FOUND: Final = object()

_MAX_LRU: Final = 50


def _lru_factory(maxsize: int = _MAX_LRU):
    """Tiny LRU keyed by single string argument (mirrors memoizeWithLRU)."""

    def decorator(fn):
        cache: dict[str, Any] = {}
        order: list[str] = []

        def wrapper(arg: str) -> Any:
            if arg in cache:
                order.remove(arg)
                order.append(arg)
                return cache[arg]
            result = fn(arg)
            cache[arg] = result
            order.append(arg)
            while len(order) > maxsize:
                old = order.pop(0)
                cache.pop(old, None)
            return result

        wrapper.cache = cache  # type: ignore[attr-defined]
        return wrapper

    return decorator


def log_for_diagnostics_no_pii(
    level: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """TS diagLogs.logForDiagnosticsNoPII — stub (no structured sink in Python port)."""
    del level, message, extra


def _path_root(path: str) -> str:
    ab = os.path.abspath(path)
    if os.name == "nt":
        drive, _ = os.path.splitdrive(ab)
        return (drive + os.sep) if drive else os.sep
    return os.sep


@_lru_factory(_MAX_LRU)
def _find_git_root_impl(start_path: str) -> str | type[_GIT_ROOT_NOT_FOUND]:
    start_time = time.time() * 1000
    log_for_diagnostics_no_pii("info", "find_git_root_started")

    current = os.path.normpath(os.path.abspath(start_path))
    root = _path_root(current)
    stat_count = 0

    def try_git(at: str) -> str | None:
        nonlocal stat_count
        git_path = os.path.join(at, ".git")
        try:
            stat_count += 1
            st = os.stat(git_path)
            if stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode):
                return unicodedata.normalize("NFC", at)
        except OSError:
            return None
        return None

    while current != root:
        found = try_git(current)
        if found is not None:
            log_for_diagnostics_no_pii(
                "info",
                "find_git_root_completed",
                {
                    "duration_ms": time.time() * 1000 - start_time,
                    "stat_count": stat_count,
                    "found": True,
                },
            )
            return found
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    found = try_git(root)
    if found is not None:
        log_for_diagnostics_no_pii(
            "info",
            "find_git_root_completed",
            {
                "duration_ms": time.time() * 1000 - start_time,
                "stat_count": stat_count,
                "found": True,
            },
        )
        return found

    log_for_diagnostics_no_pii(
        "info",
        "find_git_root_completed",
        {
            "duration_ms": time.time() * 1000 - start_time,
            "stat_count": stat_count,
            "found": False,
        },
    )
    return _GIT_ROOT_NOT_FOUND


def find_git_root(start_path: str) -> str | None:
    r = _find_git_root_impl(start_path)
    return None if r is _GIT_ROOT_NOT_FOUND else r


@_lru_factory(_MAX_LRU)
def _resolve_canonical_root(git_root: str) -> str:
    try:
        git_file = os.path.join(git_root, ".git")
        with open(git_file, encoding="utf-8", errors="replace") as f:
            git_content = f.read().strip()
        if not git_content.startswith("gitdir:"):
            return git_root
        worktree_git_dir = os.path.normpath(
            os.path.join(git_root, git_content[len("gitdir:") :].strip())
        )
        with open(os.path.join(worktree_git_dir, "commondir"), encoding="utf-8") as f:
            common_dir = os.path.normpath(
                os.path.join(worktree_git_dir, f.read().strip())
            )
        if os.path.normpath(os.path.dirname(worktree_git_dir)) != os.path.join(
            common_dir, "worktrees"
        ):
            return git_root
        with open(os.path.join(worktree_git_dir, "gitdir"), encoding="utf-8") as f:
            backlink = os.path.realpath(f.read().strip())
        if backlink != os.path.join(os.path.realpath(git_root), ".git"):
            return git_root
        if os.path.basename(common_dir) != ".git":
            return unicodedata.normalize("NFC", common_dir)
        return unicodedata.normalize("NFC", os.path.dirname(common_dir))
    except OSError:
        return git_root


def find_canonical_git_root(start_path: str) -> str | None:
    root = find_git_root(start_path)
    if not root:
        return None
    return _resolve_canonical_root(root)


@lru_cache(maxsize=1)
def git_exe() -> str:
    import shutil

    return shutil.which("git") or "git"


_is_git_memo: bool | None = None


async def get_is_git() -> bool:
    """Memoized async: cwd is inside a git work tree."""
    global _is_git_memo
    start_time = time.time() * 1000
    log_for_diagnostics_no_pii("info", "is_git_check_started")
    if _is_git_memo is not None:
        return _is_git_memo
    _is_git_memo = find_git_root(get_cwd()) is not None
    log_for_diagnostics_no_pii(
        "info",
        "is_git_check_completed",
        {"duration_ms": time.time() * 1000 - start_time, "is_git": _is_git_memo},
    )
    return _is_git_memo


async def get_git_dir(cwd: str) -> str | None:
    r = await exec_file_no_throw_with_cwd(
        git_exe(),
        ["rev-parse", "--git-dir"],
        cwd=cwd,
        preserve_output_on_error=False,
    )
    if r.get("code") != 0:
        return None
    raw = (r.get("stdout") or "").strip()
    if not raw:
        return None
    if not os.path.isabs(raw):
        raw = os.path.normpath(os.path.join(cwd, raw))
    return raw


async def is_at_git_root() -> bool:
    cwd = get_cwd()
    git_root = find_git_root(cwd)
    if not git_root:
        return False
    try:
        resolved_cwd, resolved_root = await asyncio.gather(
            asyncio.to_thread(os.path.realpath, cwd),
            asyncio.to_thread(os.path.realpath, git_root),
        )
        return resolved_cwd == resolved_root
    except OSError:
        return cwd == git_root


async def dir_is_in_git_repo(cwd: str) -> bool:
    return find_git_root(cwd) is not None


async def get_head() -> str:
    r = await exec_file_no_throw(
        git_exe(),
        ["rev-parse", "HEAD"],
        {"preserve_output_on_error": False},
    )
    return (r.get("stdout") or "").strip() if r.get("code") == 0 else ""


async def get_branch() -> str:
    r = await exec_file_no_throw(
        git_exe(),
        ["rev-parse", "--abbrev-ref", "HEAD"],
        {"preserve_output_on_error": False},
    )
    return (r.get("stdout") or "").strip() if r.get("code") == 0 else ""


async def get_default_branch() -> str:
    r = await exec_file_no_throw(
        git_exe(),
        ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
        {"preserve_output_on_error": False},
    )
    if r.get("code") == 0 and (r.get("stdout") or "").strip():
        return (r.get("stdout") or "").strip()
    for name in ("main", "master"):
        r2 = await exec_file_no_throw(
            git_exe(),
            ["rev-parse", "--verify", f"origin/{name}"],
            {"preserve_output_on_error": False},
        )
        if r2.get("code") == 0:
            return name
    return "main"


async def get_remote_url() -> str | None:
    r = await exec_file_no_throw(
        git_exe(),
        ["config", "--get", "remote.origin.url"],
        {"preserve_output_on_error": False},
    )
    out = (r.get("stdout") or "").strip()
    return out if r.get("code") == 0 and out else None


def normalize_git_remote_url(url: str) -> str | None:
    trimmed = url.strip()
    if not trimmed:
        return None

    ssh_match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", trimmed)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}".lower()

    url_match = re.match(
        r"^(?:https?|ssh)://(?:[^@]+@)?([^/]+)/(.+?)(?:\.git)?$", trimmed
    )
    if url_match:
        host = url_match.group(1) or ""
        path = url_match.group(2) or ""
        if _is_local_host(host) and path.startswith("git/"):
            proxy_path = path[4:]
            segments = proxy_path.split("/")
            if len(segments) >= 3 and "." in segments[0]:
                return proxy_path.lower()
            return f"github.com/{proxy_path}".lower()
        return f"{host}/{path}".lower()

    return None


def _is_local_host(host: str) -> bool:
    host_without_port = host.split(":")[0] if host else ""
    return host_without_port in ("localhost",) or bool(
        re.match(r"^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host_without_port)
    )


async def get_repo_remote_hash() -> str | None:
    remote_url = await get_remote_url()
    if not remote_url:
        return None
    normalized = normalize_git_remote_url(remote_url)
    if not normalized:
        return None
    h = hashlib.sha256(normalized.encode()).hexdigest()
    return h[:16]


async def get_is_head_on_remote() -> bool:
    r = await exec_file_no_throw(
        git_exe(),
        ["rev-parse", "@{u}"],
        {"preserve_output_on_error": False},
    )
    return r.get("code") == 0


async def has_unpushed_commits() -> bool:
    r = await exec_file_no_throw(
        git_exe(),
        ["rev-list", "--count", "@{u}..HEAD"],
        {"preserve_output_on_error": False},
    )
    if r.get("code") != 0:
        return False
    try:
        return int((r.get("stdout") or "").strip() or "0") > 0
    except ValueError:
        return False


async def get_is_clean(*, ignore_untracked: bool = False) -> bool:
    args = ["--no-optional-locks", "status", "--porcelain"]
    if ignore_untracked:
        args.append("-uno")
    r = await exec_file_no_throw(git_exe(), args, {"preserve_output_on_error": False})
    return len((r.get("stdout") or "").strip()) == 0


async def get_changed_files() -> list[str]:
    r = await exec_file_no_throw(
        git_exe(),
        ["--no-optional-locks", "status", "--porcelain"],
        {"preserve_output_on_error": False},
    )
    lines = []
    for line in (r.get("stdout") or "").strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) >= 2:
            lines.append(parts[1].strip())
    return [x for x in lines if x]


@dataclass
class GitFileStatus:
    tracked: list[str]
    untracked: list[str]


async def get_file_status() -> GitFileStatus:
    r = await exec_file_no_throw(
        git_exe(),
        ["--no-optional-locks", "status", "--porcelain"],
        {"preserve_output_on_error": False},
    )
    tracked: list[str] = []
    untracked: list[str] = []
    for line in (r.get("stdout") or "").strip().split("\n"):
        if not line:
            continue
        status = line[:2]
        filename = line[2:].strip()
        if status == "??":
            untracked.append(filename)
        elif filename:
            tracked.append(filename)
    return GitFileStatus(tracked=tracked, untracked=untracked)


async def get_worktree_count_from_fs() -> int:
    r = await exec_file_no_throw(
        git_exe(),
        ["worktree", "list"],
        {"preserve_output_on_error": False},
    )
    if r.get("code") != 0:
        return 1
    lines = [ln for ln in (r.get("stdout") or "").splitlines() if ln.strip()]
    return max(1, len(lines))


get_worktree_count = get_worktree_count_from_fs


async def is_shallow_clone_fs() -> bool:
    gd = await get_git_dir(get_cwd())
    if not gd:
        return False
    return os.path.isfile(os.path.join(gd, "shallow"))


async def stash_to_clean_state(message: str | None = None) -> bool:
    try:
        from datetime import datetime, timezone

        stash_message = message or (
            f"Hare auto-stash - {datetime.now(timezone.utc).isoformat()}"
        )
        untracked = (await get_file_status()).untracked
        if untracked:
            add_r = await exec_file_no_throw(
                git_exe(),
                ["add", *untracked],
                {"preserve_output_on_error": False},
            )
            if add_r.get("code") != 0:
                return False
        st_r = await exec_file_no_throw(
            git_exe(),
            ["stash", "push", "--message", stash_message],
            {"preserve_output_on_error": False},
        )
        return st_r.get("code") == 0
    except OSError:
        return False


@dataclass
class GitRepoState:
    commit_hash: str
    branch_name: str
    remote_url: str | None
    is_head_on_remote: bool
    is_clean: bool
    worktree_count: int


async def get_git_state() -> GitRepoState | None:
    try:
        (
            commit_hash,
            branch_name,
            remote_url,
            head_on_remote,
            clean,
            wt_count,
        ) = await asyncio.gather(
            get_head(),
            get_branch(),
            get_remote_url(),
            get_is_head_on_remote(),
            get_is_clean(),
            get_worktree_count_from_fs(),
        )
        return GitRepoState(
            commit_hash=commit_hash,
            branch_name=branch_name,
            remote_url=remote_url,
            is_head_on_remote=head_on_remote,
            is_clean=clean,
            worktree_count=wt_count,
        )
    except Exception:
        return None


@dataclass
class ParsedGitRemote:
    host: str
    owner: str
    name: str


def parse_git_remote(url: str) -> ParsedGitRemote | None:
    """Minimal parser for github-style URLs (stub for detectRepository.ts)."""
    n = normalize_git_remote_url(url)
    if not n:
        return None
    parts = n.split("/")
    if len(parts) < 3:
        return None
    return ParsedGitRemote(host=parts[0], owner=parts[1], name=parts[2])


async def get_github_repo() -> str | None:
    remote_url = await get_remote_url()
    if not remote_url:
        log_for_debugging("Local GitHub repo: unknown")
        return None
    parsed = parse_git_remote(remote_url)
    if parsed and parsed.host == "github.com":
        result = f"{parsed.owner}/{parsed.name}"
        log_for_debugging(f"Local GitHub repo: {result}")
        return result
    log_for_debugging("Local GitHub repo: unknown")
    return None


@dataclass
class UntrackedFileEntry:
    path: str
    content: str


@dataclass
class PreservedGitState:
    remote_base_sha: str | None
    remote_base: str | None
    patch: str
    untracked_files: list[UntrackedFileEntry]
    format_patch: str | None
    head_sha: str | None
    branch_name: str | None


_MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024
_MAX_TOTAL_SIZE_BYTES = 5 * 1024 * 1024 * 1024
_MAX_FILE_COUNT = 20000
_SNIFF_BUFFER_SIZE = 64 * 1024


async def find_remote_base() -> str | None:
    r1 = await exec_file_no_throw(
        git_exe(),
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        {"preserve_output_on_error": False},
    )
    if r1.get("code") == 0 and (r1.get("stdout") or "").strip():
        return (r1.get("stdout") or "").strip()

    r2 = await exec_file_no_throw(
        git_exe(),
        ["remote", "show", "origin", "--", "HEAD"],
        {"preserve_output_on_error": False},
    )
    if r2.get("code") == 0:
        m = re.search(r"HEAD branch: (\S+)", r2.get("stdout") or "")
        if m:
            return f"origin/{m.group(1)}"

    for candidate in ("origin/main", "origin/staging", "origin/master"):
        r3 = await exec_file_no_throw(
            git_exe(),
            ["rev-parse", "--verify", candidate],
            {"preserve_output_on_error": False},
        )
        if r3.get("code") == 0:
            return candidate
    return None


async def _capture_untracked_files() -> list[UntrackedFileEntry]:
    r = await exec_file_no_throw(
        git_exe(),
        ["ls-files", "--others", "--exclude-standard"],
        {"preserve_output_on_error": False},
    )
    if r.get("code") != 0 or not (r.get("stdout") or "").strip():
        return []

    git_root = find_git_root(get_cwd()) or get_cwd()
    files = [f for f in (r.get("stdout") or "").strip().split("\n") if f]
    result: list[UntrackedFileEntry] = []
    total_size = 0

    for file_path in files:
        if len(result) >= _MAX_FILE_COUNT:
            log_for_debugging(
                f"Untracked file capture: reached max file count ({_MAX_FILE_COUNT})"
            )
            break
        if has_binary_extension(file_path):
            continue
        abs_path = os.path.join(git_root, file_path.replace("/", os.sep))
        try:
            st = os.stat(abs_path)
            file_size = st.st_size
            if file_size > _MAX_FILE_SIZE_BYTES:
                log_for_debugging(
                    f"Untracked file capture: skipping {file_path} (exceeds max bytes)"
                )
                continue
            if total_size + file_size > _MAX_TOTAL_SIZE_BYTES:
                log_for_debugging("Untracked file capture: reached total size limit")
                break
            if file_size == 0:
                result.append(UntrackedFileEntry(path=file_path, content=""))
                continue
            sniff_size = min(_SNIFF_BUFFER_SIZE, file_size)
            with open(abs_path, "rb") as fh:
                sniff = fh.read(sniff_size)
            if is_binary_content(sniff):
                continue
            if file_size <= sniff_size:
                content = sniff.decode("utf-8", errors="replace")
            else:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            result.append(UntrackedFileEntry(path=file_path, content=content))
            total_size += file_size
        except OSError as err:
            log_for_debugging(f"Failed to read untracked file {file_path}: {err}")
    return result


async def preserve_git_state_for_issue() -> PreservedGitState | None:
    try:
        if not await get_is_git():
            return None

        if await is_shallow_clone_fs():
            log_for_debugging("Shallow clone detected, using HEAD-only mode for issue")
            patch_r, untracked = await asyncio.gather(
                exec_file_no_throw(
                    git_exe(), ["diff", "HEAD"], {"preserve_output_on_error": False}
                ),
                _capture_untracked_files(),
            )
            return PreservedGitState(
                remote_base_sha=None,
                remote_base=None,
                patch=patch_r.get("stdout") or "",
                untracked_files=untracked,
                format_patch=None,
                head_sha=None,
                branch_name=None,
            )

        remote_base = await find_remote_base()
        if not remote_base:
            log_for_debugging("No remote found, using HEAD-only mode for issue")
            patch_r, untracked = await asyncio.gather(
                exec_file_no_throw(
                    git_exe(), ["diff", "HEAD"], {"preserve_output_on_error": False}
                ),
                _capture_untracked_files(),
            )
            return PreservedGitState(
                remote_base_sha=None,
                remote_base=None,
                patch=patch_r.get("stdout") or "",
                untracked_files=untracked,
                format_patch=None,
                head_sha=None,
                branch_name=None,
            )

        mb = await exec_file_no_throw(
            git_exe(),
            ["merge-base", "HEAD", remote_base],
            {"preserve_output_on_error": False},
        )
        if mb.get("code") != 0 or not (mb.get("stdout") or "").strip():
            log_for_debugging("Merge-base failed, using HEAD-only mode for issue")
            patch_r, untracked = await asyncio.gather(
                exec_file_no_throw(
                    git_exe(), ["diff", "HEAD"], {"preserve_output_on_error": False}
                ),
                _capture_untracked_files(),
            )
            return PreservedGitState(
                remote_base_sha=None,
                remote_base=None,
                patch=patch_r.get("stdout") or "",
                untracked_files=untracked,
                format_patch=None,
                head_sha=None,
                branch_name=None,
            )

        remote_base_sha = (mb.get("stdout") or "").strip()

        patch_r, untracked, fmt_r, head_r, branch_r = await asyncio.gather(
            exec_file_no_throw(
                git_exe(),
                ["diff", remote_base_sha],
                {"preserve_output_on_error": False},
            ),
            _capture_untracked_files(),
            exec_file_no_throw(
                git_exe(),
                ["format-patch", f"{remote_base_sha}..HEAD", "--stdout"],
                {"preserve_output_on_error": False},
            ),
            exec_file_no_throw(
                git_exe(), ["rev-parse", "HEAD"], {"preserve_output_on_error": False}
            ),
            exec_file_no_throw(
                git_exe(),
                ["rev-parse", "--abbrev-ref", "HEAD"],
                {"preserve_output_on_error": False},
            ),
        )

        format_patch: str | None = None
        if fmt_r.get("code") == 0 and (fmt_r.get("stdout") or "").strip():
            format_patch = fmt_r.get("stdout") or ""

        trimmed_branch = (branch_r.get("stdout") or "").strip()
        head_sha = (head_r.get("stdout") or "").strip() or None
        branch_name = (
            trimmed_branch if trimmed_branch and trimmed_branch != "HEAD" else None
        )

        return PreservedGitState(
            remote_base_sha=remote_base_sha,
            remote_base=remote_base,
            patch=patch_r.get("stdout") or "",
            untracked_files=untracked,
            format_patch=format_patch,
            head_sha=head_sha,
            branch_name=branch_name,
        )
    except Exception as err:
        log_error(err if isinstance(err, Exception) else RuntimeError(str(err)))
        return None


def get_fs_implementation() -> Any:
    """Stub for fsOperations.getFsImplementation — returns os-like module."""
    return os


def is_current_directory_bare_git_repo() -> bool:
    """Detect cwd layout that could make Git treat it as a bare repo (security)."""
    fs = get_fs_implementation()
    cwd = get_cwd()
    git_path = os.path.join(cwd, ".git")
    try:
        st = fs.stat(git_path)
        if stat.S_ISREG(st.st_mode):
            return False
        if stat.S_ISDIR(st.st_mode):
            git_head_path = os.path.join(git_path, "HEAD")
            try:
                if stat.S_ISREG(fs.stat(git_head_path).st_mode):
                    return False
            except OSError:
                pass
    except OSError:
        pass

    try:
        st_h = fs.stat(os.path.join(cwd, "HEAD"))
        if stat.S_ISREG(st_h.st_mode):
            return True
    except OSError:
        pass
    try:
        st_o = fs.stat(os.path.join(cwd, "objects"))
        if stat.S_ISDIR(st_o.st_mode):
            return True
    except OSError:
        pass
    try:
        st_r = fs.stat(os.path.join(cwd, "refs"))
        if stat.S_ISDIR(st_r.st_mode):
            return True
    except OSError:
        pass
    return False


__all__ = [
    "GitFileStatus",
    "GitRepoState",
    "ParsedGitRemote",
    "PreservedGitState",
    "UntrackedFileEntry",
    "dir_is_in_git_repo",
    "find_canonical_git_root",
    "find_git_root",
    "find_remote_base",
    "get_branch",
    "get_changed_files",
    "get_default_branch",
    "get_file_status",
    "get_git_dir",
    "get_git_state",
    "get_github_repo",
    "get_head",
    "get_is_clean",
    "get_is_git",
    "get_is_head_on_remote",
    "get_remote_url",
    "get_repo_remote_hash",
    "get_worktree_count",
    "get_worktree_count_from_fs",
    "git_exe",
    "has_unpushed_commits",
    "is_at_git_root",
    "is_current_directory_bare_git_repo",
    "is_shallow_clone_fs",
    "normalize_git_remote_url",
    "parse_git_remote",
    "preserve_git_state_for_issue",
    "stash_to_clean_state",
]
