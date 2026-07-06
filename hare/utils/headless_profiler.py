"""Headless print-mode profiling — port of `headlessProfiler.ts`."""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import is_env_truthy

# Detailed profiling (same env as startup profiler)
DETAILED_PROFILING = is_env_truthy(os.environ.get("CLAUDE_CODE_PROFILE_STARTUP"))

STATSIG_SAMPLE_RATE = 0.05
STATSIG_LOGGING_SAMPLED = (
    os.environ.get("USER_TYPE") == "ant" or random.random() < STATSIG_SAMPLE_RATE
)
SHOULD_PROFILE = DETAILED_PROFILING or STATSIG_LOGGING_SAMPLED

MARK_PREFIX = "headless_"
_current_turn = -1
_marks: list[tuple[str, float]] = []


def _get_is_non_interactive_session() -> bool:
    try:
        from hare.bootstrap.state import get_is_non_interactive_session  # type: ignore[import-not-found]

        return get_is_non_interactive_session()
    except ImportError:
        return not os.isatty(0)


def log_event(_name: str, _meta: Any) -> None:
    pass


def json_stringify(obj: Any, *_a: Any, **_k: Any) -> str:
    return json.dumps(obj)


def headless_profiler_start_turn() -> None:
    global _current_turn, _marks
    if not _get_is_non_interactive_session() or not SHOULD_PROFILE:
        return
    _current_turn += 1
    _marks.clear()
    _marks.append((f"{MARK_PREFIX}turn_start", time.perf_counter() * 1000))
    if DETAILED_PROFILING:
        log_for_debugging(f"[headlessProfiler] Started turn {_current_turn}")


def headless_profiler_checkpoint(name: str) -> None:
    if not _get_is_non_interactive_session() or not SHOULD_PROFILE:
        return
    _marks.append((f"{MARK_PREFIX}{name}", time.perf_counter() * 1000))
    if DETAILED_PROFILING:
        log_for_debugging(f"[headlessProfiler] Checkpoint: {name}")


def log_headless_profiler_turn() -> None:
    if not _get_is_non_interactive_session() or not SHOULD_PROFILE:
        return
    times: dict[str, float] = {}
    for name, t in _marks:
        times[name[len(MARK_PREFIX) :]] = t
    turn_start = times.get("turn_start")
    if turn_start is None:
        return
    metadata: dict[str, Any] = {"turn_number": _current_turn}
    sm = times.get("system_message_yielded")
    if sm is not None and _current_turn == 0:
        metadata["time_to_system_message_ms"] = round(sm)
    qs = times.get("query_started")
    if qs is not None:
        metadata["time_to_query_start_ms"] = round(qs - turn_start)
    fc = times.get("first_chunk")
    if fc is not None:
        metadata["time_to_first_response_ms"] = round(fc - turn_start)
    ar = times.get("api_request_sent")
    if qs is not None and ar is not None:
        metadata["query_overhead_ms"] = round(ar - qs)
    metadata["checkpoint_count"] = len(_marks)
    ep = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
    if ep:
        metadata["entrypoint"] = ep
    if STATSIG_LOGGING_SAMPLED:
        log_event("tengu_headless_latency", metadata)
    if DETAILED_PROFILING:
        log_for_debugging(
            f"[headlessProfiler] Turn {_current_turn} metrics: {json_stringify(metadata)}"
        )
