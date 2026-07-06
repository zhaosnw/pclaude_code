"""Minimal performance timing helpers (port of profilerBase.ts)."""

from __future__ import annotations

import time
from collections import namedtuple
from typing import Any

Mark = namedtuple("Mark", ["name", "start_time"])

_perf_singleton: Any = None


def get_performance() -> Any:
    global _perf_singleton
    if _perf_singleton is None:
        _perf_singleton = _Perf()
    return _perf_singleton


class _Perf:
    def __init__(self) -> None:
        self._marks: list[Mark] = []

    def mark(self, name: str) -> None:
        self._marks.append(Mark(name, time.perf_counter() * 1000.0))

    def get_entries_by_type(self, typ: str) -> list[Mark]:
        if typ != "mark":
            return []
        return list(self._marks)


def format_ms(ms: float) -> str:
    return f"{ms:.1f}"


def format_timeline_line(
    start_time: float,
    delta: float,
    name: str,
    _memory: Any,
    _w1: int,
    _w2: int,
) -> str:
    return f"{format_ms(start_time)}ms  +{format_ms(delta)}ms  {name}"
