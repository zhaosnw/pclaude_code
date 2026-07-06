"""
Team memory sync – synchronize HARE.md across team members.

Port of: src/services/teamMemorySync/index.ts
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from hare.services.team_memory_sync.watcher import (
    BatchWatcher,
    ChangeType,
    FileChange,
    TeamMemoryWatcher,
)
from hare.services.team_memory_sync.types import TeamMemorySyncState


@dataclass
class TeamMemorySyncService:
    """Coordinates team memory file watching and broadcast.

    Lifecycle::

        svc = TeamMemorySyncService(team_dir="/team/shared")
        await svc.start()       # begins watching the team dir
        ...
        await svc.stop()        # stops watchers and cleans up
    """

    team_dir: str = ""
    memory_file: str = "HARE.md"
    _watchers: list[TeamMemoryWatcher] = field(default_factory=list)
    _batch: BatchWatcher = field(default_factory=BatchWatcher)
    _state: TeamMemorySyncState = field(default_factory=TeamMemorySyncState)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start watching the team memory directory for changes."""
        if not self.team_dir:
            return

        team_path = Path(self.team_dir).resolve()
        memory_path = team_path / self.memory_file

        # Watch the team directory for any .md changes (recursive)
        dir_watcher = TeamMemoryWatcher(
            roots=[team_path],
            patterns=["*.md", "*.json", "*.yaml", "*.yml"],
            interval=2.0,
            recursive=True,
        )
        dir_watcher.on_change(self._on_team_file_changed)
        self._watchers.append(dir_watcher)
        self._batch.add(dir_watcher)

        # Also track the specific memory file directly (faster detection
        # when it is the only thing that changes)
        if memory_path.exists() or memory_path.parent.exists():
            file_watcher = TeamMemoryWatcher(
                roots=[memory_path],
                patterns=[self.memory_file],
                interval=1.0,
                recursive=False,
            )
            file_watcher.on_change(self._on_memory_file_changed)
            self._watchers.append(file_watcher)
            self._batch.add(file_watcher)

        await self._batch.start_all()

    async def stop(self) -> None:
        """Stop all watchers and release resources."""
        await self._batch.stop_all()
        self._watchers.clear()
        self._state = TeamMemorySyncState()

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    async def broadcast_update(self, content: str) -> None:
        """Write updated content to the shared team memory file."""
        memory_path = os.path.join(self.team_dir, self.memory_file)
        os.makedirs(os.path.dirname(memory_path) or ".", exist_ok=True)
        try:
            with open(memory_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError:
            pass

    async def read_shared_memory(self) -> str:
        """Read the shared team memory file."""
        memory_path = os.path.join(self.team_dir, self.memory_file)
        try:
            with open(memory_path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    async def _on_team_file_changed(self, change: FileChange) -> None:
        """Handle any file change within the team directory."""
        rel = change.path.relative_to(Path(self.team_dir).resolve())
        self._state.pending_paths.append(str(rel))
        if change.change_type == ChangeType.CREATED:
            self._state.pending_paths.append(f"+{rel}")
        elif change.change_type == ChangeType.DELETED:
            self._state.pending_paths.append(f"-{rel}")

    async def _on_memory_file_changed(self, change: FileChange) -> None:
        """Handle a change to the primary memory file."""
        self._state.last_sync_revision = str(change.timestamp)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def tracked_files(self) -> list[str]:
        """Return the list of files currently being tracked."""
        files: list[str] = []
        for w in self._watchers:
            files.extend(str(p) for p in w.get_tracked_files())
        return sorted(set(files))


async def sync_team_memory(team_dir: str) -> None:
    """One-shot sync: scan the team directory for any changed files.

    If changes are found they are recorded in a temporary
    ``TeamMemorySyncState`` and the caller is expected to reconcile.
    """
    team_path = Path(team_dir).resolve()
    watcher = TeamMemoryWatcher(
        roots=[team_path],
        patterns=["*.md", "*.json", "*.yaml", "*.yml"],
        interval=2.0,
        recursive=True,
    )
    changes = await watcher.scan()
    if changes:
        state = TeamMemorySyncState()
        for c in changes:
            try:
                rel = c.path.relative_to(team_path)
            except ValueError:
                rel = c.path
            state.pending_paths.append(str(rel))
