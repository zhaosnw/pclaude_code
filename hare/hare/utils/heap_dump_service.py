"""Heap dump + memory diagnostics — port of `heapDumpService.ts`."""

from __future__ import annotations

import gc
import json
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from hare.utils.debug import log_for_debugging
from hare.utils.log import log_error

Trigger = Literal["manual", "auto-1.5GB"]
CC_VERSION = "2.1.88"
_T0 = time.time()


@dataclass
class MemoryDiagnostics:
    timestamp: str
    session_id: str
    trigger: Trigger
    dump_number: int
    uptime_seconds: float
    memory_usage: dict[str, int]
    memory_growth_rate: dict[str, float]
    v8_heap_stats: dict[str, int]
    v8_heap_spaces: list[dict[str, Any]] | None = None
    resource_usage: dict[str, float] = field(default_factory=dict)
    active_handles: int = 0
    active_requests: int = 0
    open_file_descriptors: int | None = None
    analysis: dict[str, Any] = field(default_factory=dict)
    smaps_rollup: str | None = None
    platform: str = ""
    node_version: str = ""
    cc_version: str = CC_VERSION


def _get_session_id() -> str:
    try:
        from hare.bootstrap.state import get_session_id  # type: ignore[import-not-found]

        return get_session_id()
    except ImportError:
        return "no-session"


def _memory_info() -> dict[str, int]:
    try:
        import psutil  # type: ignore[import-not-found]

        p = psutil.Process()
        rss = int(p.memory_info().rss)
        return {
            "heapUsed": rss // 2,
            "heapTotal": rss,
            "external": 0,
            "arrayBuffers": 0,
            "rss": rss,
        }
    except Exception:
        return {
            "heapUsed": 0,
            "heapTotal": 0,
            "external": 0,
            "arrayBuffers": 0,
            "rss": 0,
        }


async def capture_memory_diagnostics(
    trigger: Trigger, dump_number: int = 0
) -> MemoryDiagnostics:
    import platform

    usage = _memory_info()
    uptime = time.time() - _T0
    rss = usage["rss"]
    bps = rss / uptime if uptime > 0 else 0.0
    mb_h = (bps * 3600) / (1024 * 1024)
    leaks: list[str] = []
    if mb_h > 100:
        leaks.append(f"High memory growth rate: {mb_h:.1f} MB/hour")

    from datetime import datetime, timezone

    return MemoryDiagnostics(
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=_get_session_id(),
        trigger=trigger,
        dump_number=dump_number,
        uptime_seconds=uptime,
        memory_usage=usage,
        memory_growth_rate={"bytesPerSecond": bps, "mbPerHour": mb_h},
        v8_heap_stats={
            "heapSizeLimit": usage["heapTotal"],
            "mallocedMemory": 0,
            "peakMallocedMemory": 0,
            "detachedContexts": 0,
            "nativeContexts": 0,
        },
        analysis={
            "potentialLeaks": leaks,
            "recommendation": "Python: use tracemalloc / memray for heap analysis.",
        },
        platform=sys.platform,
        node_version=platform.python_version(),
    )


@dataclass
class HeapDumpResult:
    success: bool
    heap_path: str | None = None
    diag_path: str | None = None
    error: str | None = None


async def perform_heap_dump(
    trigger: Trigger = "manual", dump_number: int = 0
) -> HeapDumpResult:
    try:
        diag = await capture_memory_diagnostics(trigger, dump_number)
        log_for_debugging(f"[HeapDump] {diag.analysis.get('recommendation', '')}")
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        sid = diag.session_id
        suffix = f"-dump{dump_number}" if dump_number > 0 else ""
        diag_path = desktop / f"{sid}{suffix}-diagnostics.json"
        heap_path = desktop / f"{sid}{suffix}.heapsnapshot"
        diag_path.write_text(json.dumps(asdict(diag), indent=2), encoding="utf-8")
        if not tracemalloc.is_tracing():
            tracemalloc.start(25)
        snap = tracemalloc.take_snapshot()
        top = snap.statistics("lineno")[:2000]
        heap_path.write_text("\n".join(str(x) for x in top), encoding="utf-8")
        gc.collect()
        return HeapDumpResult(
            success=True, heap_path=str(heap_path), diag_path=str(diag_path)
        )
    except Exception as e:
        ex = e if isinstance(e, Exception) else RuntimeError(str(e))
        log_error(ex)
        return HeapDumpResult(success=False, error=str(ex))
