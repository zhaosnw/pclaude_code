"""
In-memory file read cache keyed by path with mtime invalidation.

Port of: src/utils/fileReadCache.ts
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


def _detect_file_encoding(file_path: str) -> str:
    """Best-effort encoding; full port lives in file.ts."""
    return "utf-8"


@dataclass
class _Cached:
    content: str
    encoding: str
    mtime: float


class FileReadCache:
    def __init__(self, max_cache_size: int = 1000) -> None:
        self._cache: dict[str, _Cached] = {}
        self._max_cache_size = max_cache_size

    def read_file(self, file_path: str) -> dict[str, str]:
        try:
            st = os.stat(file_path)
        except OSError:
            self._cache.pop(file_path, None)
            raise
        mtime_ms = st.st_mtime * 1000
        hit = self._cache.get(file_path)
        if hit and hit.mtime == mtime_ms:
            return {"content": hit.content, "encoding": hit.encoding}
        enc = _detect_file_encoding(file_path)
        with open(file_path, encoding=enc, errors="replace") as f:
            content = f.read().replace("\r\n", "\n")
        self._cache[file_path] = _Cached(content=content, encoding=enc, mtime=mtime_ms)
        if len(self._cache) > self._max_cache_size:
            first = next(iter(self._cache))
            del self._cache[first]
        return {"content": content, "encoding": enc}

    def clear(self) -> None:
        self._cache.clear()

    def invalidate(self, file_path: str) -> None:
        self._cache.pop(file_path, None)

    def get_stats(self) -> dict[str, Any]:
        return {"size": len(self._cache), "entries": list(self._cache.keys())}


file_read_cache = FileReadCache()
