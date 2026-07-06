"""Hot-path set utilities (`set.ts`)."""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def difference(a: set[T], b: set[T]) -> set[T]:
    return {x for x in a if x not in b}


def intersects(a: set[T], b: set[T]) -> bool:
    if not a or not b:
        return False
    return not a.isdisjoint(b)


def every(a: frozenset[T] | set[T], b: frozenset[T] | set[T]) -> bool:
    return a.issubset(b)


def union(a: set[T], b: set[T]) -> set[T]:
    return a | b
