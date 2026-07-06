"""Port of: src/utils/array.ts"""

from __future__ import annotations
from typing import Any, Callable, Sequence, TypeVar

T = TypeVar("T")


def count(items: Sequence[T], predicate: Callable[[T], bool]) -> int:
    return sum(1 for item in items if predicate(item))


def unique_by(items: Sequence[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    result = []
    for item in items:
        k = item.get(key)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result
