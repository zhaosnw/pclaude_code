"""Non-PII diagnostic log lines (`diagLogs.ts`)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, TypeVar

T = TypeVar("T")

DiagnosticLogLevel = Literal["debug", "info", "warn", "error"]


def _get_diagnostic_log_file() -> str | None:
    return os.environ.get("CLAUDE_CODE_DIAGNOSTICS_FILE")


def log_for_diagnostics_no_pii(
    level: DiagnosticLogLevel,
    event: str,
    data: dict[str, Any] | None = None,
) -> None:
    log_file = _get_diagnostic_log_file()
    if not log_file:
        return
    entry = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "level": level,
        "event": event,
        "data": data or {},
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    path = Path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


async def with_diagnostics_timing(
    event: str,
    fn: Callable[[], Awaitable[T]],
    get_data: Callable[[T], dict[str, Any]] | None = None,
) -> T:
    import time

    start = time.perf_counter()
    log_for_diagnostics_no_pii("info", f"{event}_started")
    try:
        result = await fn()
        extra = get_data(result) if get_data else {}
        log_for_diagnostics_no_pii(
            "info",
            f"{event}_completed",
            {"duration_ms": int((time.perf_counter() - start) * 1000), **extra},
        )
        return result
    except BaseException:
        log_for_diagnostics_no_pii(
            "error",
            f"{event}_failed",
            {"duration_ms": int((time.perf_counter() - start) * 1000)},
        )
        raise
