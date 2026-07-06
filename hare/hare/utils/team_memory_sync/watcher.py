"""
Low-level file-system watching primitives for team memory sync.

Port of: src/utils/teamMemorySync/watcher.ts

Provides snapshot/diff, content-aware change detection, debounced
polling with hash-based integrity checks, and one-shot change collection.
Unlike the service-layer TeamMemoryWatcher, this module supplies
stateless building blocks: directory snapshots, structured diffs,
single-shot collectors, and stability predicates.

Typical usage::

    # Build a baseline snapshot
    baseline = snapshot_dir(root, patterns=["*.md", "*.json"])

    # ... time passes, files are written ...

    # Collect changes via polling
    async for batch in watch_and_collect([root], patterns=["*.md"]):
        for path, kind in batch:
            print(f"{kind.name}: {path}")

    # Or do a one-shot synchronous check
    after = snapshot_dir(root, patterns=["*.md", "*.json"])
    diff = compute_diff(baseline, after)
    print(f"Added {len(diff.added)}, modified {len(diff.modified)}, "
          f"removed {len(diff.removed)}")

    # Wait for a directory to settle
    if is_directory_stable(root, settle_seconds=3.0):
        print("No recent writes — safe to snapshot.")

    # Validate snapshot integrity
    stale = validate_snapshot(after)
    if stale:
        print(f"{len(stale)} files have changed or disappeared since snapshot")
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import AsyncIterator


class ChangeKind(Enum):
    ADDED = auto()
    MODIFIED = auto()
    REMOVED = auto()


@dataclass(frozen=True)
class FileRecord:
    """Content fingerprint of a single file."""

    path: Path
    mtime_ns: int
    size: int
    sha256: str
    seen_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class DirSnapshot:
    """Point-in-time picture of a directory tree keyed by resolved Path."""

    root: Path
    files: dict[Path, FileRecord]
    taken_at: float

    def __contains__(self, path: Path) -> bool:
        return path in self.files


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class WatcherError(Exception):
    """Base exception for watcher operations."""


class SnapshotIntegrityError(WatcherError):
    """Raised when a snapshot fails integrity verification.

    *stale_paths* contains the paths whose content no longer matches
    the recorded hash, or that no longer exist on the filesystem.
    """

    def __init__(self, stale_paths: list[Path]) -> None:
        self.stale_paths = stale_paths
        super().__init__(
            f"Snapshot integrity check failed: "
            f"{len(stale_paths)} file(s) have changed or disappeared"
        )


# ---------------------------------------------------------------------------
# Structured diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotDiff:
    """Structured result of comparing two DirSnapshots.

    All lists are sorted by path for determinism.  *modified* only
    includes files whose content hash differs — mtime-only changes are
    excluded.
    """

    added: list[Path]
    modified: list[Path]
    removed: list[Path]

    @property
    def empty(self) -> bool:
        """True when no changes of any kind were detected."""
        return not (self.added or self.modified or self.removed)

    @property
    def total_changes(self) -> int:
        """Total number of file-level changes (added + modified + removed)."""
        return len(self.added) + len(self.modified) + len(self.removed)

    def by_kind(self, kind: ChangeKind) -> list[Path]:
        """Convenience: return the list for a specific :class:`ChangeKind`."""
        if kind == ChangeKind.ADDED:
            return self.added
        if kind == ChangeKind.MODIFIED:
            return self.modified
        return self.removed


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _hash_file(path: Path) -> str | None:
    """Streaming SHA-256 (64 KiB chunks). Returns None on I/O error."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


_EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


def _make_record(path: Path) -> FileRecord | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size == 0:
        return FileRecord(
            path=path, mtime_ns=stat.st_mtime_ns, size=0,
            sha256=_EMPTY_SHA256, seen_at=time.time(),
        )
    digest = _hash_file(path)
    if digest is None:
        return None
    return FileRecord(
        path=path, mtime_ns=stat.st_mtime_ns, size=stat.st_size,
        sha256=digest, seen_at=time.time(),
    )


def snapshot_dir(
    root: Path,
    *,
    patterns: list[str] | None = None,
    recursive: bool = True,
) -> DirSnapshot:
    """Walk *root* and capture a DirSnapshot of matching files.

    *patterns* are globs against base file names (None = all files).
    If *root* is a single file and it matches, only that file is captured.
    """
    resolved = root.resolve()
    pats = patterns or ["*"]
    files: dict[Path, FileRecord] = {}

    if resolved.is_file():
        if _matches(resolved.name, pats):
            rec = _make_record(resolved)
            if rec is not None:
                files[resolved] = rec
        return DirSnapshot(root=resolved, files=files, taken_at=time.time())

    if not resolved.is_dir():
        return DirSnapshot(root=resolved, files=files, taken_at=time.time())

    iterator = resolved.rglob if recursive else resolved.glob
    for entry in iterator("*"):
        if not entry.is_file() or not _matches(entry.name, pats):
            continue
        rec = _make_record(entry)
        if rec is not None:
            files[entry] = rec
    return DirSnapshot(root=resolved, files=files, taken_at=time.time())


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def compute_diff(
    before: DirSnapshot,
    after: DirSnapshot,
) -> SnapshotDiff:
    """Compare two snapshots and return a structured :class:`SnapshotDiff`.

    *modified* only includes files whose content hash differs — mtime-only
    changes are ignored.  Root paths must match or be compatible — comparing
    snapshots of unrelated directory trees is allowed but callers should
    ensure the meaning is sensible.
    """
    before_keys = set(before.files.keys())
    after_keys = set(after.files.keys())

    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    modified: list[Path] = []

    for path in before_keys & after_keys:
        if before.files[path].sha256 != after.files[path].sha256:
            modified.append(path)
    modified.sort()
    return SnapshotDiff(added=added, modified=modified, removed=removed)


# ---------------------------------------------------------------------------
# Debounced polling collector
# ---------------------------------------------------------------------------


def _arm_debounce(
    pending: dict[Path, tuple[ChangeKind, float]],
    path: Path,
    kind: ChangeKind,
    now: float,
) -> None:
    """Insert or refresh a pending change, collapsing redundant events.

    Upgrade rules:
      ADDED + MODIFIED → MODIFIED
      MODIFIED + REMOVED → REMOVED
      REMOVED + ADDED → MODIFIED
    """
    existing = pending.get(path)
    if existing is None:
        pending[path] = (kind, now)
        return
    prev_kind, _ = existing
    if prev_kind == ChangeKind.REMOVED and kind == ChangeKind.ADDED:
        pending[path] = (ChangeKind.MODIFIED, now)
    elif prev_kind == ChangeKind.ADDED and kind == ChangeKind.MODIFIED:
        pending[path] = (ChangeKind.MODIFIED, now)
    elif prev_kind == ChangeKind.MODIFIED and kind == ChangeKind.REMOVED:
        pending[path] = (ChangeKind.REMOVED, now)
    else:
        pending[path] = (kind, now)


async def watch_and_collect(
    roots: list[Path],
    *,
    patterns: list[str] | None = None,
    interval: float = 2.0,
    debounce_seconds: float = 0.5,
    recursive: bool = True,
) -> AsyncIterator[list[tuple[Path, ChangeKind]]]:
    """Poll *roots* on *interval* and yield batches of debounced changes.

    Each batch is a list of (path, ChangeKind) tuples. Rapid writes within
    *debounce_seconds* are collapsed so a path appears only once per batch,
    after the dust settles. The iterator runs indefinitely — cancel or
    break to stop.
    """
    pats = patterns or ["*"]

    # Seed empty "before" so existing files appear as ADDED on first poll.
    before_snapshots: dict[Path, DirSnapshot] = {
        root.resolve(): DirSnapshot(root=root.resolve(), files={}, taken_at=0.0)
        for root in roots
    }
    pending: dict[Path, tuple[ChangeKind, float]] = {}

    while True:
        now = time.time()
        batch: list[tuple[Path, ChangeKind]] = []

        for root in roots:
            resolved = root.resolve()
            before = before_snapshots.get(
                resolved,
                DirSnapshot(root=resolved, files={}, taken_at=0.0),
            )
            after = snapshot_dir(resolved, patterns=pats, recursive=recursive)
            before_snapshots[resolved] = after

            diff = compute_diff(before, after)
            for p in diff.added:
                _arm_debounce(pending, p, ChangeKind.ADDED, now)
            for p in diff.modified:
                _arm_debounce(pending, p, ChangeKind.MODIFIED, now)
            for p in diff.removed:
                _arm_debounce(pending, p, ChangeKind.REMOVED, now)

        # Emit expired debounce entries.
        expired = [
            p for p, (_, ts) in pending.items()
            if now - ts >= debounce_seconds
        ]
        for path in expired:
            kind, _ = pending.pop(path)
            batch.append((path, kind))

        if batch:
            yield batch
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Snapshot integrity
# ---------------------------------------------------------------------------


def validate_snapshot(snap: DirSnapshot) -> list[Path]:
    """Verify that every file in *snap* still exists with the same content.

    Returns a (possibly empty) list of paths that have **diverged** —
    either the file was deleted, its hash changed, or it can no longer
    be read.  An empty list means the snapshot is still valid against
    the current filesystem state.

    Use this before acting on a stale snapshot to avoid propagating
    changes against a moving target.
    """
    stale: list[Path] = []
    for path, record in snap.files.items():
        current = _make_record(path)
        if current is None:
            stale.append(path)
        elif current.sha256 != record.sha256:
            stale.append(path)
    return stale


def verify_snapshot_or_raise(snap: DirSnapshot) -> None:
    """Like :func:`validate_snapshot` but raises :class:`SnapshotIntegrityError`
    when any file has changed since the snapshot was taken.
    """
    stale = validate_snapshot(snap)
    if stale:
        raise SnapshotIntegrityError(stale)


# ---------------------------------------------------------------------------
# One-shot synchronous change detection
# ---------------------------------------------------------------------------


# Module-level baseline cache for find_changes(). Grows without bound
# by design — callers that need many distinct roots should manage
# snapshots explicitly with snapshot_dir / compute_diff.
_baseline_cache: dict[Path, DirSnapshot] = {}


def find_changes(
    root: Path,
    *,
    patterns: list[str] | None = None,
    recursive: bool = True,
) -> SnapshotDiff:
    """Take a snapshot of *root* and compare against a previously
    stored baseline if one exists — otherwise return a diff where every
    file is marked as added.

    This is the synchronous single-call equivalent of one iteration of
    :func:`watch_and_collect`.  Unlike :func:`compute_diff` which needs
    both snapshots up front, ``find_changes`` manages the baseline
    internally via a module-level cache keyed on the resolved root.

    .. caution::

        The baseline cache is global and grows without bound.  For
        long-running processes with many distinct roots, prefer
        :func:`watch_and_collect` or manage snapshots explicitly.
    """
    resolved = root.resolve()
    before = _baseline_cache.get(resolved)
    after = snapshot_dir(resolved, patterns=patterns, recursive=recursive)
    _baseline_cache[resolved] = after

    if before is None:
        # First observation — every file is "added"
        return SnapshotDiff(
            added=sorted(after.files.keys()),
            modified=[],
            removed=[],
        )
    return compute_diff(before, after)


def clear_baseline_cache() -> None:
    """Reset the internal baseline cache used by :func:`find_changes`."""
    _baseline_cache.clear()


# ---------------------------------------------------------------------------
# Directory stability
# ---------------------------------------------------------------------------


def is_directory_stable(
    root: Path,
    *,
    settle_seconds: float = 2.0,
    patterns: list[str] | None = None,
    recursive: bool = True,
) -> bool:
    """Check whether *root* appears stable — no matching file has been
    modified within the last *settle_seconds*.

    Useful as a guard before taking a snapshot in an environment where
    multiple processes may be writing simultaneously (e.g. team memory
    sync where other Claude sessions write to a shared directory).

    A non-existent root is considered stable (nothing to write to).
    An empty directory is also stable.
    """
    resolved = root.resolve()
    if not resolved.exists():
        return True

    now = time.time()
    threshold = now - settle_seconds
    pats = patterns or ["*"]

    if resolved.is_file():
        if not _matches(resolved.name, pats):
            return True
        try:
            return resolved.stat().st_mtime <= threshold
        except OSError:
            return True

    if not resolved.is_dir():
        return True

    iterator = resolved.rglob if recursive else resolved.glob
    for entry in iterator("*"):
        if not entry.is_file() or not _matches(entry.name, pats):
            continue
        try:
            if entry.stat().st_mtime > threshold:
                return False
        except OSError:
            # Unreadable — can't confirm stability, assume unstable
            return False
    return True


async def wait_until_stable(
    root: Path,
    *,
    settle_seconds: float = 2.0,
    max_wait: float = 30.0,
    poll_interval: float = 0.25,
    patterns: list[str] | None = None,
    recursive: bool = True,
) -> bool:
    """Block until *root* stabilises or *max_wait* elapses.

    Returns ``True`` if the directory settled within the deadline,
    ``False`` on timeout.
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if is_directory_stable(
            root,
            settle_seconds=settle_seconds,
            patterns=patterns,
            recursive=recursive,
        ):
            return True
        await asyncio.sleep(poll_interval)
    return False


# ---------------------------------------------------------------------------
# One-shot async collector
# ---------------------------------------------------------------------------


async def watch_once(
    roots: list[Path],
    *,
    patterns: list[str] | None = None,
    debounce_seconds: float = 0.5,
    settle_seconds: float = 2.0,
    max_wait: float = 30.0,
    recursive: bool = True,
) -> list[tuple[Path, ChangeKind]]:
    """Collect a single batch of changes and return.

    Waits for each root to stabilise (no recent writes), then captures a
    final snapshot and diffs against a fresh-from-scratch baseline.
    Because the baseline is taken after stabilisation, the returned
    changes reflect only files that were created or modified between the
    stabilisation snapshot and the final capture — typically none.

    If you need to detect what changed since a known point in time, pass
    explicit snapshots to :func:`compute_diff` instead.

    Returns an empty list on timeout or if no changes are detected.
    """
    stable = await wait_until_stable(
        roots[0] if len(roots) == 1 else roots[0],
        settle_seconds=settle_seconds,
        max_wait=max_wait,
        patterns=patterns,
        recursive=recursive,
    )
    if not stable:
        return []

    baseline_snapshots: dict[Path, DirSnapshot] = {}
    for root in roots:
        resolved = root.resolve()
        baseline_snapshots[resolved] = snapshot_dir(
            resolved, patterns=patterns, recursive=recursive,
        )

    await asyncio.sleep(debounce_seconds)

    batch: list[tuple[Path, ChangeKind]] = []
    for root in roots:
        resolved = root.resolve()
        before = baseline_snapshots[resolved]
        after = snapshot_dir(resolved, patterns=patterns, recursive=recursive)
        diff = compute_diff(before, after)
        for p in diff.added:
            batch.append((p, ChangeKind.ADDED))
        for p in diff.modified:
            batch.append((p, ChangeKind.MODIFIED))
        for p in diff.removed:
            batch.append((p, ChangeKind.REMOVED))
    return batch


# ---------------------------------------------------------------------------
# Convenience: snapshot from explicit file list
# ---------------------------------------------------------------------------


def snapshot_from_paths(
    paths: list[Path],
    *,
    root: Path | None = None,
) -> DirSnapshot:
    """Build a :class:`DirSnapshot` containing only the given *paths*.

    Each path is resolved and a :class:`FileRecord` is computed for it
    (``None``-valued records from unreadable files are silently skipped).

    *root* determines the snapshot root.  When ``None`` the nearest
    common ancestor directory of all given paths is used; if no paths
    are supplied or all paths fail to read, the current working
    directory is used.
    """
    files: dict[Path, FileRecord] = {}
    resolved_paths: list[Path] = []
    for p in paths:
        rp = p.resolve()
        rec = _make_record(rp)
        if rec is not None:
            files[rp] = rec
            resolved_paths.append(rp)

    if root is not None:
        return DirSnapshot(
            root=root.resolve(), files=files, taken_at=time.time(),
        )

    if not resolved_paths:
        return DirSnapshot(
            root=Path.cwd(), files=files, taken_at=time.time(),
        )

    # Nearest common ancestor directory
    ancestor = resolved_paths[0].parent
    for p in resolved_paths[1:]:
        while ancestor not in p.parents and ancestor != Path(ancestor.root):
            ancestor = ancestor.parent
    return DirSnapshot(root=ancestor, files=files, taken_at=time.time())
