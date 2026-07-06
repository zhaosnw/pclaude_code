"""
Object.groupBy-style grouping. Port of src/utils/objectGroupBy.ts.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import Callable, TypeVar

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


def object_group_by(
    items: Iterable[T],
    key_selector: Callable[[T, int], K],
) -> dict[K, list[T]]:
    result: dict[K, list[T]] = {}
    index = 0
    for item in items:
        key = key_selector(item, index)
        index += 1
        if key not in result:
            result[key] = []
        result[key].append(item)
    return result
