"""
Filesystem watcher for team memory files.

Port of: src/services/teamMemorySync/watcher.ts

Provides polling-based file watching for team memory directories.
Tracks file additions, modifications, and deletions using mtime + content
hashing. Supports recursive directory walking, glob pattern filtering, and
debouncing to suppress events for in-progress writes.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Awaitable, Callable


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ChangeType(Enum):
    """Kind of filesystem event detected."""

    CREATED = auto()
    MODIFIED = auto()
    DELETED = auto()


@dataclass(frozen=True)
class FileChange:
    """A single detected file change event."""

    path: Path
    change_type: ChangeType
    timestamp: float = field(default_factory=time.time)


ChangeCallback = Callable[[FileChange], Awaitable[None]]
SyncChangeCallback = Callable[[Path], None]


# ---------------------------------------------------------------------------
# Internal tracking
# ---------------------------------------------------------------------------


@dataclass
class _FileRecord:
    """Snapshot of a file at its last known-good state."""

    mtime: float
    hash: str
    size: int


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class TeamMemoryWatcher:
    """Watches team memory directories for file changes.

    Polls the filesystem at a configurable interval, comparing mtime
    and content hashes to detect creates, modifications, and deletions.
    Supports recursive directory walking, glob pattern filtering, and
    debounce windows to suppress events for in-progress writes.

    Usage::

        watcher = TeamMemoryWatcher(
            roots=[Path("/team/memory")],
            patterns=["*.md", "*.json"],
        )
        watcher.on_change(my_async_handler)
        await watcher.start()
        ...
        await watcher.stop()
    """

    def __init__(
        self,
        roots: list[Path],
        *,
        patterns: list[str] | None = None,
        interval: float = 2.0,
        debounce_ms: float = 500.0,
        recursive: bool = True,
        hash_algorithm: str = "sha256",
    ) -> None:
        self._roots = [Path(r).resolve() for r in roots]
        self._patterns = patterns if patterns is not None else ["*"]
        self._interval = interval
        self._debounce_seconds = debounce_ms / 1000.0
        self._recursive = recursive
        self._hash_algorithm = hash_algorithm

        # Internal state
        self._files: dict[Path, _FileRecord] = {}
        self._pending: dict[Path, float] = {}  # path -> mtime for debounce
        self._callbacks: list[ChangeCallback] = []
        self._sync_callbacks: list[SyncChangeCallback] = []

        # Lifecycle
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """Whether the polling loop is active."""
        return self._running

    @property
    def roots(self) -> list[Path]:
        """The root paths being watched."""
        return list(self._roots)

    def on_change(self, cb: ChangeCallback) -> None:
        """Register an async callback invoked on each file change event.

        The callback receives a :class:`FileChange` with path, change
        type, and timestamp. Exceptions from callbacks are caught and
        discarded so one failing callback does not break the loop.
        """
        self._callbacks.append(cb)

    def on_change_sync(self, cb: SyncChangeCallback) -> None:
        """Register a synchronous callback (backward-compatible).

        Receives only the :class:`Path` that changed, not the full
        ``FileChange`` event.
        """
        self._sync_callbacks.append(cb)

    async def start(self) -> None:
        """Begin polling.  No-op if already running."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop polling and release internal state."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._files.clear()
        self._pending.clear()

    async def scan(self) -> list[FileChange]:
        """Perform a one-shot scan of all roots.

        Returns the list of changes detected since the last scan.  Also
        fires registered callbacks for each change found.

        This method is safe to call while the polling loop is running
        (e.g. for an immediate on-demand check).
        """
        changes: list[FileChange] = []
        now = time.time()

        # ---- discover currently-existing matching files ---------------
        current: set[Path] = set()
        for root in self._roots:
            if not root.exists():
                continue
            if root.is_dir():
                iterator = root.rglob if self._recursive else root.glob
                glob_arg = "*"
                for candidate in iterator(glob_arg):
                    if candidate.is_file() and self._matches_filter(candidate):
                        current.add(candidate)
            elif root.is_file() and self._matches_filter(root):
                current.add(root)

        # ---- detect creates & modifications ---------------------------
        for path in current:
            try:
                stat = path.stat()
                mtime = stat.st_mtime
                size = stat.st_size
            except OSError:
                continue

            record = self._files.get(path)
            if record is None:
                # Newly discovered file — emit CREATED and arm debounce
                # timer so rapid follow-up writes are suppressed.
                self._files[path] = _FileRecord(
                    mtime=mtime,
                    hash=self._compute_hash(path),
                    size=size,
                )
                self._pending[path] = now
                changes.append(
                    FileChange(path=path, change_type=ChangeType.CREATED)
                )
            elif mtime > record.mtime or size != record.size:
                # mtime advanced or size changed — debounce before acting
                if path in self._pending:
                    if now - self._pending[path] < self._debounce_seconds:
                        # Still within the debounce window: update the
                        # record silently so we do not re-fire on the
                        # next scan, but do NOT emit an event.
                        new_hash = self._compute_hash(path)
                        self._files[path] = _FileRecord(
                            mtime=mtime, hash=new_hash, size=size,
                        )
                        self._pending[path] = now
                        continue

                # Outside debounce window (or first change seen) —
                # verify content and emit if actually different.
                new_hash = self._compute_hash(path)
                if new_hash != record.hash:
                    self._files[path] = _FileRecord(
                        mtime=mtime, hash=new_hash, size=size,
                    )
                    self._pending[path] = now
                    changes.append(
                        FileChange(path=path, change_type=ChangeType.MODIFIED)
                    )
                else:
                    # mtime bumped but content unchanged (e.g. touch)
                    self._files[path] = _FileRecord(
                        mtime=mtime, hash=record.hash, size=size,
                    )

        # ---- detect deletions -----------------------------------------
        removed = set(self._files.keys()) - current
        for path in removed:
            self._files.pop(path, None)
            self._pending.pop(path, None)
            changes.append(
                FileChange(path=path, change_type=ChangeType.DELETED)
            )

        # ---- fire callbacks -------------------------------------------
        for change in changes:
            for cb in self._callbacks:
                try:
                    await cb(change)
                except Exception:
                    pass
            for cb in self._sync_callbacks:
                try:
                    cb(change.path)
                except Exception:
                    pass

        return changes

    def get_tracked_files(self) -> list[Path]:
        """Return currently-tracked file paths (sorted for determinism)."""
        return sorted(self._files.keys())

    def reset(self) -> None:
        """Clear all tracked state.

        The next scan will treat every file as newly created.
        """
        self._files.clear()
        self._pending.clear()

    def add_root(self, root: Path) -> None:
        """Add a root path to watch.

        Already-resolved roots are silently ignored.  The watcher does
        not need to be restarted; the new root will be picked up on the
        next poll cycle.
        """
        resolved = root.resolve()
        if resolved not in self._roots:
            self._roots.append(resolved)

    def remove_root(self, root: Path) -> None:
        """Remove a root path from the watch list.

        Files tracked under the removed root are **not** immediately
        pruned — they will naturally expire as DELETED on the next scan
        (since they will no longer appear in the file set).
        """
        resolved = root.resolve()
        try:
            self._roots.remove(resolved)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _matches_filter(self, path: Path) -> bool:
        """Check whether a path matches any of the configured glob patterns."""
        name = path.name
        return any(fnmatch.fnmatch(name, pat) for pat in self._patterns)

    def _compute_hash(self, path: Path) -> str:
        """Compute a content hash for a file.

        Uses a streaming read to avoid loading large files entirely into
        memory. Returns an empty string on any read error (permission,
        missing, etc.).
        """
        try:
            h = hashlib.new(self._hash_algorithm)
        except ValueError:
            h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    async def _poll_loop(self) -> None:
        """Internal endless loop that calls :meth:`scan` on the interval."""
        while self._running:
            try:
                await self.scan()
            except Exception:
                # Swallow unexpected errors to keep the loop alive.
                # Individual callback errors are already caught in scan().
                pass
            await asyncio.sleep(self._interval)


# ---------------------------------------------------------------------------
# Convenience function (backward-compatible with original stub signature)
# ---------------------------------------------------------------------------


async def watch_team_memory_paths(
    roots: list[Path],
    on_change: Callable[[Path], None],
    *,
    interval: float = 2.0,
    patterns: list[str] | None = None,
    recursive: bool = True,
) -> asyncio.Task[None]:
    """Watch team memory paths for changes and invoke *on_change* per file.

    Returns a running :class:`asyncio.Task` that polls the filesystem.
    The caller can cancel the task to stop watching.

    This function retains the original signature from the stub for
    backward compatibility while using ``TeamMemoryWatcher`` internally.
    """
    watcher = TeamMemoryWatcher(
        roots,
        patterns=patterns,
        interval=interval,
        recursive=recursive,
    )
    watcher.on_change_sync(on_change)
    await watcher.start()
    # The watcher owns its task; it is the caller's responsibility to
    # cancel the returned handle when watching should stop.
    assert watcher._task is not None  # set by start()
    return watcher._task


# ---------------------------------------------------------------------------
# Batch watcher — coordinates multiple TeamMemoryWatcher instances
# ---------------------------------------------------------------------------


class BatchWatcher:
    """Manages a collection of ``TeamMemoryWatcher`` instances.

    Provides a unified start/stop/scan interface for watching multiple
    disjoint directory trees with different filter configurations.

    Used by ``TeamMemorySyncService`` to coordinate the main team memory
    directory watcher alongside any project-level CLAUDE.md watchers.
    """

    def __init__(self) -> None:
        self._watchers: list[TeamMemoryWatcher] = []

    def add(self, watcher: TeamMemoryWatcher) -> None:
        """Register a watcher to manage."""
        self._watchers.append(watcher)

    async def start_all(self) -> None:
        """Start every registered watcher."""
        for w in self._watchers:
            await w.start()

    async def stop_all(self) -> None:
        """Stop every registered watcher and clear the list."""
        for w in self._watchers:
            await w.stop()
        self._watchers.clear()

    async def scan_all(self) -> dict[Path, list[FileChange]]:
        """Scan every watcher. Returns a dict keyed by root path."""
        result: dict[Path, list[FileChange]] = {}
        for w in self._watchers:
            for root in w.roots:
                result.setdefault(root, []).extend(await w.scan())
        return result

    @property
    def watchers(self) -> list[TeamMemoryWatcher]:
        return list(self._watchers)
