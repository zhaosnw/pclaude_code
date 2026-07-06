"""
Query pipeline timing (env CLAUDE_CODE_PROFILE_QUERY=1). Port of src/utils/queryProfiler.ts.
"""

from __future__ import annotations

import os
import time

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import is_env_truthy
from hare.utils.profiler_base import format_ms, format_timeline_line

ENABLED = is_env_truthy(os.environ.get("CLAUDE_CODE_PROFILE_QUERY"))

_memory_snapshots: dict[str, dict[str, int]] = {}
_query_count = 0
_first_token_time: float | None = None
_marks: list[tuple[str, float]] = []


def _now() -> float:
    return time.perf_counter() * 1000.0


def _mem() -> dict[str, int]:
    try:
        r = resource.getrusage(resource.RUSAGE_SELF)
        return {
            "rss": int(r.ru_maxrss * 1024) if os.name != "nt" else int(r.ru_maxrss),
            "heapUsed": 0,
        }
    except Exception:
        return {"rss": 0, "heapUsed": 0}


def start_query_profile() -> None:
    global _query_count, _first_token_time, _marks
    if not ENABLED:
        return
    _marks.clear()
    _memory_snapshots.clear()
    _first_token_time = None
    _query_count += 1
    query_checkpoint("query_user_input_received")


def query_checkpoint(name: str) -> None:
    global _first_token_time
    if not ENABLED:
        return
    t = _now()
    _marks.append((name, t))
    _memory_snapshots[name] = _mem()
    if name == "query_first_chunk_received" and _first_token_time is None and _marks:
        _first_token_time = _marks[-1][1]


def end_query_profile() -> None:
    if not ENABLED:
        return
    query_checkpoint("query_profile_end")


def _slow_warning(delta_ms: float, name: str) -> str:
    if name == "query_user_input_received":
        return ""
    if delta_ms > 1000:
        return " ⚠️  VERY SLOW"
    if delta_ms > 100:
        return " ⚠️  SLOW"
    if "git_status" in name and delta_ms > 50:
        return " ⚠️  git status"
    if "tool_schema" in name and delta_ms > 50:
        return " ⚠️  tool schemas"
    if "client_creation" in name and delta_ms > 50:
        return " ⚠️  client creation"
    return ""


def _report() -> str:
    if not ENABLED:
        return "Query profiling not enabled (set CLAUDE_CODE_PROFILE_QUERY=1)"
    if not _marks:
        return "No query profiling checkpoints recorded"
    lines = ["=" * 80, f"QUERY PROFILING REPORT - Query #{_query_count}", "=" * 80, ""]
    baseline = _marks[0][1]
    prev = baseline
    api_sent = 0.0
    first_chunk = 0.0
    for name, mt in _marks:
        rel = mt - baseline
        delta = mt - prev
        mem = _memory_snapshots.get(name)
        lines.append(
            format_timeline_line(
                rel, delta, name, mem, 10, 9, _slow_warning(delta, name)
            ),
        )
        if name == "query_api_request_sent":
            api_sent = rel
        if name == "query_first_chunk_received":
            first_chunk = rel
        prev = mt
    lines.append("")
    lines.append("-" * 80)
    if first_chunk > 0:
        pre = api_sent
        net = first_chunk - api_sent
        lines.append(f"Total TTFT: {format_ms(first_chunk)}ms")
        lines.append(
            f"  - Pre-request overhead: {format_ms(pre)}ms ({(pre / first_chunk * 100):.1f}%)",
        )
        lines.append(
            f"  - Network latency: {format_ms(net)}ms ({(net / first_chunk * 100):.1f}%)",
        )
    else:
        total = _marks[-1][1] - baseline if _marks else 0
        lines.append(f"Total time: {format_ms(total)}ms")
    lines.append("=" * 80)
    return "\n".join(lines)


def log_query_profile_report() -> None:
    if not ENABLED:
        return
    log_for_debugging(_report())
