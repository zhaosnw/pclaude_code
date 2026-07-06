"""
LRU-ish file content state keyed by normalized paths.

Port of: src/utils/fileStateCache.ts
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterator

READ_FILE_STATE_CACHE_SIZE = 100
DEFAULT_MAX_CACHE_SIZE_BYTES = 25 * 1024 * 1024


@dataclass
class FileState:
    content: str
    timestamp: float
    offset: int | None
    limit: int | None
    is_partial_view: bool | None = None


def _norm(key: str) -> str:
    return os.path.normpath(key)


class FileStateCache:
    def __init__(self, max_entries: int, max_size_bytes: int) -> None:
        self._max_entries = max_entries
        self._max_size_bytes = max_size_bytes
        self._calculated_size = 0
        self._order: OrderedDict[str, FileState] = OrderedDict()

    def get(self, key: str) -> FileState | None:
        k = _norm(key)
        if k not in self._order:
            return None
        self._order.move_to_end(k)
        return self._order[k]

    def set(self, key: str, value: FileState) -> FileStateCache:
        k = _norm(key)
        size = max(1, len(value.content.encode("utf-8", errors="replace")))
        if k in self._order:
            old = self._order.pop(k)
            self._calculated_size -= max(
                1, len(old.content.encode("utf-8", errors="replace"))
            )
        self._order[k] = value
        self._calculated_size += size
        self._evict()
        return self

    def _evict(self) -> None:
        while len(self._order) > self._max_entries or (
            self._max_size_bytes and self._calculated_size > self._max_size_bytes
        ):
            if not self._order:
                break
            old_k, old_v = self._order.popitem(last=False)
            self._calculated_size -= max(
                1, len(old_v.content.encode("utf-8", errors="replace"))
            )

    def has(self, key: str) -> bool:
        return _norm(key) in self._order

    def delete(self, key: str) -> bool:
        k = _norm(key)
        if k not in self._order:
            return False
        v = self._order.pop(k)
        self._calculated_size -= max(
            1, len(v.content.encode("utf-8", errors="replace"))
        )
        return True

    def clear(self) -> None:
        self._order.clear()
        self._calculated_size = 0

    @property
    def size(self) -> int:
        return len(self._order)

    @property
    def max(self) -> int:
        return self._max_entries

    @property
    def max_size(self) -> int:
        return self._max_size_bytes

    @property
    def calculated_size(self) -> int:
        return self._calculated_size

    def keys(self) -> Iterator[str]:
        return iter(self._order.keys())

    def entries(self) -> Iterator[tuple[str, FileState]]:
        return iter(self._order.items())

    def dump(self) -> list[tuple[str, FileState]]:
        return list(self._order.items())

    def load(self, entries: list[tuple[str, FileState]]) -> None:
        self.clear()
        for k, v in entries:
            self.set(k, v)


def create_file_state_cache_with_size_limit(
    max_entries: int,
    max_size_bytes: int = DEFAULT_MAX_CACHE_SIZE_BYTES,
) -> FileStateCache:
    return FileStateCache(max_entries, max_size_bytes)


def cache_to_object(cache: FileStateCache) -> dict[str, FileState]:
    return dict(cache.entries())


def cache_keys(cache: FileStateCache) -> list[str]:
    return list(cache.keys())


def clone_file_state_cache(cache: FileStateCache) -> FileStateCache:
    cloned = create_file_state_cache_with_size_limit(cache.max, cache.max_size)
    cloned.load(cache.dump())
    return cloned


def merge_file_state_caches(
    first: FileStateCache, second: FileStateCache
) -> FileStateCache:
    merged = clone_file_state_cache(first)
    for file_path, file_state in second.entries():
        existing = merged.get(file_path)
        if not existing or file_state.timestamp > existing.timestamp:
            merged.set(file_path, file_state)
    return merged
