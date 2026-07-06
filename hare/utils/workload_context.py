"""Turn-scoped workload tag (port of workloadContext.ts — uses contextvars)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Callable, TypeVar

T = TypeVar("T")

WORKLOAD_CRON = "cron"

_workload_var: ContextVar[str | None] = ContextVar("workload", default=None)


def get_workload() -> str | None:
    return _workload_var.get()


def run_with_workload(workload: str | None, fn: Callable[[], T]) -> T:
    token = _workload_var.set(workload)
    try:
        return fn()
    finally:
        _workload_var.reset(token)
