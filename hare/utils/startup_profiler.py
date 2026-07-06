"""Startup profiling (port of startupProfiler.ts)."""

from __future__ import annotations

import os
import random
from pathlib import Path

from hare.bootstrap import state as bootstrap_state
from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir, is_env_truthy
from hare.utils.profiler_base import format_ms, format_timeline_line, get_performance
from hare.utils.slow_operations import write_file_sync_deprecated

DETAILED_PROFILING = is_env_truthy(os.environ.get("CLAUDE_CODE_PROFILE_STARTUP"))
STATSIG_SAMPLE_RATE = 0.005
STATSIG_LOGGING_SAMPLED = (
    os.environ.get("USER_TYPE") == "ant" or random.random() < STATSIG_SAMPLE_RATE
)
SHOULD_PROFILE = DETAILED_PROFILING or STATSIG_LOGGING_SAMPLED

_memory_snapshots: list[dict[str, int]] = []
_reported = False

PHASE_DEFINITIONS = {
    "import_time": ("cli_entry", "main_tsx_imports_loaded"),
    "init_time": ("init_function_start", "init_function_end"),
    "settings_time": ("eagerLoadSettings_start", "eagerLoadSettings_end"),
    "total_time": ("cli_entry", "main_after_run"),
}

if SHOULD_PROFILE:
    _perf = get_performance()
    _perf.mark("profiler_initialized")


def profile_checkpoint(name: str) -> None:
    if not SHOULD_PROFILE:
        return
    get_performance().mark(name)
    if DETAILED_PROFILING:
        _memory_snapshots.append({})


def profile_report() -> None:
    global _reported
    if _reported:
        return
    _reported = True
    log_startup_perf()
    if DETAILED_PROFILING:
        path = get_startup_perf_log_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write_file_sync_deprecated(path, _get_report(), encoding="utf-8", flush=True)
        log_for_debugging("Startup profiling report:")
        log_for_debugging(_get_report())


def is_detailed_profiling_enabled() -> bool:
    return DETAILED_PROFILING


def get_startup_perf_log_path() -> str:
    return str(
        Path(get_hare_config_home_dir())
        / "startup-perf"
        / f"{bootstrap_state.get_session_id()}.txt"
    )


def log_startup_perf() -> None:
    if not STATSIG_LOGGING_SAMPLED:
        return
    perf = get_performance()
    marks = perf.get_entries_by_type("mark")
    if not marks:
        return
    times = {m.name: m.start_time for m in marks}
    metadata: dict[str, float | int | None] = {}
    for phase, (a, b) in PHASE_DEFINITIONS.items():
        sa, sb = times.get(a), times.get(b)
        if sa is not None and sb is not None:
            metadata[f"{phase}_ms"] = round(sb - sa)
    metadata["checkpoint_count"] = len(marks)
    try:
        from hare.services.analytics import log_event

        log_event("tengu_startup_perf", metadata)
    except ImportError:
        pass


def _get_report() -> str:
    if not DETAILED_PROFILING:
        return "Startup profiling not enabled"
    perf = get_performance()
    marks = perf.get_entries_by_type("mark")
    if not marks:
        return "No profiling checkpoints recorded"
    lines = ["=" * 80, "STARTUP PROFILING REPORT", "=" * 80, ""]
    prev = 0.0
    for i, mark in enumerate(marks):
        mem = _memory_snapshots[i] if i < len(_memory_snapshots) else None
        lines.append(
            format_timeline_line(
                mark.start_time,
                mark.start_time - prev,
                mark.name,
                mem,
                8,
                7,
            )
        )
        prev = mark.start_time
    lines.append("")
    lines.append(f"Total startup time: {format_ms(marks[-1].start_time)}ms")
    lines.append("=" * 80)
    return "\n".join(lines)
