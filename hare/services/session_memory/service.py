"""
Session memory – manages HARE.md files and session memory context.

Port of: src/services/SessionMemory/SessionMemory.ts
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class SessionMemoryService:
    cwd: str = ""
    _memory_files: list[str] = field(default_factory=list)
    _content: str = ""

    async def load(self) -> str:
        """Load all HARE.md files in the project hierarchy."""
        parts: list[str] = []
        search_dirs = self._get_search_dirs()
        for d in search_dirs:
            path = os.path.join(d, "HARE.md")
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if content:
                        parts.append(content)
                        self._memory_files.append(path)
                except OSError:
                    pass
        self._content = "\n\n---\n\n".join(parts)
        return self._content

    def _get_search_dirs(self) -> list[str]:
        """Walk up from cwd to home directory collecting search dirs."""
        dirs: list[str] = []
        current = self.cwd or os.getcwd()
        home = os.path.expanduser("~")
        while True:
            dirs.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            if len(current) < len(home):
                break
            current = parent
        dirs.reverse()
        return dirs

    @property
    def content(self) -> str:
        return self._content

    @property
    def memory_files(self) -> list[str]:
        return list(self._memory_files)


def get_session_memory(cwd: str = "") -> SessionMemoryService:
    return SessionMemoryService(cwd=cwd or os.getcwd())
