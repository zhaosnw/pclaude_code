"""
Git bundle creation and application for teleport state transfer.

Port of: src/utils/teleport/gitBundle.ts

Flow: sweep stale refs → empty-repo guard → capture WIP (stash create) →
tiered bundle (--all → HEAD → squashed-root) → upload → cleanup.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal

from hare.utils.exec_file_no_throw import exec_file_no_throw_with_cwd
from hare.utils.git import find_git_root
from hare.utils.log import log_error_msg, log_warning

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

BundleScope = Literal["all", "head", "squashed"]
BundleFailReason = Literal["git_error", "too_large", "empty_repo"]


@dataclass
class GitBundleInfo:
    path: str
    valid: bool
    heads: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    prerequisite_commits: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BundleCreateResult:
    path: str
    success: bool
    size_bytes: int = 0
    heads_included: list[str] = field(default_factory=list)
    scope: BundleScope | None = None
    error: str | None = None


@dataclass
class BundleApplyResult:
    path: str
    success: bool
    refs_updated: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BundleUploadResult:
    success: bool
    file_id: str = ""
    bundle_size_bytes: int = 0
    scope: BundleScope | None = None
    has_wip: bool = False
    error: str = ""
    fail_reason: BundleFailReason | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GIT = "git"
DEFAULT_BUNDLE_MAX_BYTES = 100 * 1024 * 1024
_STASH_REF = "refs/seed/stash"
_ROOT_REF = "refs/seed/root"
_SEED_REFS = (_STASH_REF, _ROOT_REF)

# Callback signature: (local_path, relative_name) -> (ok, file_id, size, error)
UploadFileFn = Callable[[str, str], Awaitable[tuple[bool, str, int, str]]]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

async def _run_git(args: list[str], cwd: str, *, timeout: int = 600_000) -> tuple[int, str, str]:
    r = await exec_file_no_throw_with_cwd(_GIT, args, cwd=cwd, preserve_output_on_error=True, timeout=timeout)
    return r.get("code", 1), r.get("stdout", ""), r.get("stderr", "")


def _resolve_path(raw: str) -> str:
    return os.path.abspath(os.path.expanduser(raw))


def _get_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


async def _sweep_stale_refs(git_root: str) -> None:
    """Remove stale refs/seed/* left by a prior crashed run."""
    for ref in _SEED_REFS:
        code, _, stderr = await _run_git(["update-ref", "-d", ref], git_root)
        if code != 0:
            log_warning(f"git_bundle: sweep {ref} failed: {stderr.strip()}")


async def _repo_has_any_ref(git_root: str) -> bool:
    code, stdout, _ = await _run_git(["for-each-ref", "--count=1", "refs/"], git_root)
    return code == 0 and stdout.strip() != ""


# ---------------------------------------------------------------------------
# Basic bundle primitives
# ---------------------------------------------------------------------------

async def create_git_bundle(
    cwd: str,
    output_path: str,
    *,
    include_all: bool = False,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    force: bool = False,
    progress: bool = False,
    timeout: int | None = None,
) -> BundleCreateResult:
    """Create a git bundle. *include_all* → ``--all``; *base_ref* → incremental."""
    out = _resolve_path(output_path)
    args: list[str] = ["bundle", "create"]
    if force:
        args.append("--force")
    if progress:
        args.append("--progress")

    if include_all and base_ref:
        log_warning("git_bundle: include_all and base_ref are mutually exclusive")
        base_ref = None

    if include_all:
        args.append("--all")
    elif base_ref:
        args.extend([out, f"{base_ref}..{head_ref}"])
    else:
        args.extend([out, head_ref])

    code, stdout, stderr = await _run_git(args, cwd, timeout=timeout or 600_000)
    if code != 0:
        log_error_msg(f"git bundle create failed: {stderr.strip()}")
        return BundleCreateResult(path=out, success=False, error=stderr.strip() or f"exit code {code}")

    heads = [line.strip() for line in stdout.splitlines() if line.strip()]
    return BundleCreateResult(path=out, success=True, size_bytes=_get_size(out), heads_included=heads)


async def verify_git_bundle(
    bundle_path: str, *, cwd: str | None = None, timeout: int | None = None,
) -> GitBundleInfo:
    """Verify a bundle; return heads, refs, and prerequisite commits."""
    bp = _resolve_path(bundle_path)
    workdir = cwd or str(Path(bp).parent)
    code, stdout, stderr = await _run_git(["bundle", "verify", bp], workdir, timeout=timeout or 600_000)
    if code != 0:
        log_error_msg(f"git bundle verify failed: {stderr.strip()}")
        return GitBundleInfo(path=bp, valid=False, error=stderr.strip() or f"exit code {code}")

    heads: list[str] = []
    refs: list[str] = []
    prereqs: list[str] = []
    parsing_prereqs = False
    for line in stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            parsing_prereqs = "requires these refs" in s
            continue
        if parsing_prereqs:
            prereqs.append(s)
            continue
        refs.append(s)
        parts = s.split()
        if parts:
            heads.append(parts[-1])
    return GitBundleInfo(path=bp, valid=True, heads=heads, refs=refs, prerequisite_commits=prereqs)


async def apply_git_bundle(
    bundle_path: str, cwd: str, *, dry_run: bool = False, timeout: int | None = None,
) -> BundleApplyResult:
    """Unbundle into the repository at *cwd*."""
    bp = _resolve_path(bundle_path)
    args = ["bundle", "unbundle"]
    if dry_run:
        args.append("--dry-run")
    args.append(bp)
    code, stdout, stderr = await _run_git(args, cwd, timeout=timeout or 600_000)
    if code != 0:
        log_error_msg(f"git bundle unbundle failed: {stderr.strip()}")
        return BundleApplyResult(path=bp, success=False, error=stderr.strip() or f"exit code {code}")
    refs = [line.strip() for line in stdout.splitlines() if line.strip()]
    return BundleApplyResult(path=bp, success=True, refs_updated=refs)


async def teleport_create_bundle(
    cwd: str, output_dir: str, *, filename: str = "teleport.bundle",
    base_ref: str | None = None, head_ref: str = "HEAD", force: bool = True,
) -> BundleCreateResult:
    os.makedirs(output_dir, exist_ok=True)
    return await create_git_bundle(
        cwd, os.path.join(output_dir, filename),
        include_all=(base_ref is None), base_ref=base_ref, head_ref=head_ref, force=force,
    )


async def teleport_apply_bundle(
    bundle_path: str, cwd: str, *, verify_first: bool = True,
) -> BundleApplyResult:
    bp = _resolve_path(bundle_path)
    if verify_first:
        info = await verify_git_bundle(bp, cwd=cwd)
        if not info.valid:
            return BundleApplyResult(path=bp, success=False, error=f"Bundle verification failed: {info.error}")
    return await apply_git_bundle(bp, cwd)


# ---------------------------------------------------------------------------
# Seed-bundle engine: --all → HEAD → squashed-root fallback
# ---------------------------------------------------------------------------

async def _capture_wip(git_root: str) -> tuple[str, bool]:
    """Capture working-tree changes via ``git stash create``.

    Does not touch ``refs/stash`` or the working tree.  Untracked excluded.
    Returns ``(stash_sha, has_wip)``; on error returns ``("", False)``.
    """
    code, stdout, stderr = await _run_git(["stash", "create"], git_root)
    sha = stdout.strip() if code == 0 else ""
    has_wip = sha != ""
    if code != 0:
        log_warning(f"git_bundle: stash create failed (code {code}), no-WIP: {stderr[:200]}")
    elif has_wip:
        rc, _, re = await _run_git(["update-ref", _STASH_REF, sha], git_root)
        if rc != 0:
            log_warning(f"git_bundle: update-ref {_STASH_REF} failed: {re.strip()}")
    return sha, has_wip


async def _build_squashed_bundle(git_root: str, bundle_path: str, has_wip: bool) -> tuple[int, str]:
    """Last-resort: single parentless-commit bundle from tree snapshot."""
    tree = f"{_STASH_REF}^{{tree}}" if has_wip else "HEAD^{{tree}}"
    code, stdout, stderr = await _run_git(["commit-tree", tree, "-m", "seed"], git_root)
    if code != 0:
        return code, stderr[:200]
    code, _, stderr = await _run_git(["update-ref", _ROOT_REF, stdout.strip()], git_root)
    if code != 0:
        return code, stderr[:200]
    code, _, stderr = await _run_git(["bundle", "create", bundle_path, _ROOT_REF], git_root)
    return code, "" if code == 0 else stderr[:200]


async def _bundle_with_fallback(
    git_root: str, bundle_path: str, max_bytes: int, has_wip: bool,
) -> BundleCreateResult:
    """Tiered creation: --all → HEAD → squashed-root, each only if prior exceeds max_bytes."""
    extra = [_STASH_REF] if has_wip else []

    async def _mk(base: str) -> tuple[int, str, str]:
        return await _run_git(["bundle", "create", bundle_path, base, *extra], git_root)

    # Tier 1: --all
    code, _, stderr = await _mk("--all")
    if code != 0:
        return BundleCreateResult(path=bundle_path, success=False,
                                  error=f"git bundle --all failed ({code}): {stderr[:200]}")
    if (sz := _get_size(bundle_path)) <= max_bytes:
        return BundleCreateResult(path=bundle_path, success=True, size_bytes=sz, scope="all")
    log_warning(f"git_bundle: --all {sz / 2**20:.1f}MB > {max_bytes / 2**20:.0f}MB → HEAD")

    # Tier 2: HEAD
    code, _, stderr = await _mk("HEAD")
    if code != 0:
        return BundleCreateResult(path=bundle_path, success=False,
                                  error=f"git bundle HEAD failed ({code}): {stderr[:200]}")
    if (sz := _get_size(bundle_path)) <= max_bytes:
        return BundleCreateResult(path=bundle_path, success=True, size_bytes=sz, scope="head")
    log_warning(f"git_bundle: HEAD {sz / 2**20:.1f}MB → squashed-root")

    # Tier 3: squashed-root
    code, stderr = await _build_squashed_bundle(git_root, bundle_path, has_wip)
    if code != 0:
        return BundleCreateResult(path=bundle_path, success=False,
                                  error=f"git bundle squashed-root failed ({code}): {stderr}")
    if (sz := _get_size(bundle_path)) <= max_bytes:
        return BundleCreateResult(path=bundle_path, success=True, size_bytes=sz, scope="squashed")

    return BundleCreateResult(path=bundle_path, success=False,
                              error="Repo too large to bundle. Set up GitHub on https://claude.ai/code")


async def _default_upload(_p: str, _n: str) -> tuple[bool, str, int, str]:
    return False, "", 0, "No upload callback configured"


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

async def create_and_upload_git_bundle(
    *,
    cwd: str | None = None,
    upload: UploadFileFn | None = None,
    max_bytes: int | None = None,
) -> BundleUploadResult:
    """Bundle repo and upload for CCR seed-bundle seeding.

    Full flow: locate git root → sweep stale refs → empty-repo guard → capture
    WIP (git stash create → refs/seed/stash) → tiered bundle (--all → HEAD →
    squashed-root) → upload → cleanup.

    Parameters
    ----------
    cwd: Working directory for git-root discovery.
    upload: ``(local_path, relative_name) -> (ok, file_id, size, error)``.
    max_bytes: Max bundle bytes (default 100 MB or TENGU_CCR_BUNDLE_MAX_BYTES).
    """
    up = upload or _default_upload
    if max_bytes is None:
        env = os.environ.get("TENGU_CCR_BUNDLE_MAX_BYTES", "")
        max_bytes = int(env) if env.isdigit() else DEFAULT_BUNDLE_MAX_BYTES

    # 1. Locate git root.
    git_root = await find_git_root(cwd or os.getcwd())
    if not git_root:
        return BundleUploadResult(success=False, error="Not in a git repository", fail_reason="git_error")

    # 2. Sweep stale refs from prior crashed runs.
    await _sweep_stale_refs(git_root)

    # 3. Empty-repo guard.
    if not await _repo_has_any_ref(git_root):
        return BundleUploadResult(success=False, error="Repository has no commits yet", fail_reason="empty_repo")

    # 4. Capture WIP.
    _wip_sha, has_wip = await _capture_wip(git_root)

    # 5. Tiered bundle creation.
    bundle_path = _resolve_path(os.path.join(tempfile.gettempdir(), f"ccr-seed-{os.urandom(8).hex()}.bundle"))

    bundle_result = BundleCreateResult(path=bundle_path, success=False)
    try:
        bundle_result = await _bundle_with_fallback(git_root, bundle_path, max_bytes, has_wip)
        if not bundle_result.success:
            reason: BundleFailReason = "too_large" if bundle_result.error and "too large" in bundle_result.error.lower() else "git_error"
            return BundleUploadResult(success=False, error=bundle_result.error or "Bundle creation failed", fail_reason=reason)
    finally:
        ok, fid, usz, uerr = False, "", 0, ""
        if bundle_result and bundle_result.success:
            ok, fid, usz, uerr = await up(bundle_path, "_source_seed.bundle")
        try:
            os.unlink(bundle_path)
        except OSError:
            pass
        for ref in _SEED_REFS:
            await _run_git(["update-ref", "-d", ref], git_root)

    if not ok:
        return BundleUploadResult(success=False, error=uerr or "Bundle upload failed", fail_reason="git_error")
    return BundleUploadResult(
        success=True, file_id=fid, bundle_size_bytes=usz, scope=bundle_result.scope, has_wip=has_wip,
    )
