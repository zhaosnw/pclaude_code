"""Slow JSON/clone operation logging (port of slowOperations.ts)."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from contextlib import contextmanager
from typing import Any, Iterator

from hare.utils.debug import log_for_debugging


def _threshold_ms() -> float:
    raw = os.environ.get("CLAUDE_CODE_SLOW_OPERATION_THRESHOLD_MS")
    if raw is not None:
        try:
            v = float(raw)
            if v >= 0:
                return v
        except ValueError:
            pass
    if os.environ.get("NODE_ENV") == "development":
        return 20.0
    if os.environ.get("USER_TYPE") == "ant":
        return 300.0
    return float("inf")


SLOW_OPERATION_THRESHOLD_MS = _threshold_ms()

_is_logging = False


def caller_frame(stack: str | None) -> str:
    if not stack:
        return ""
    for line in stack.split("\n"):
        if "slow_operations" in line:
            continue
        m = re.search(r"([^/\\]+?):(\d+):\d+\)?$", line)
        if m:
            return f" @ {m.group(1)}:{m.group(2)}"
    return ""


def _build_description(template: str, *values: Any) -> str:
    # Simplified tagged-template description
    out = template
    for v in values:
        if isinstance(v, list):
            out += f"Array[{len(v)}]"
        elif isinstance(v, dict):
            out += f"Object{{{len(v)} keys}}"
        elif isinstance(v, str) and len(v) > 80:
            out += v[:80] + "…"
        else:
            out += str(v)
    return out


@contextmanager
def slow_logging(template: str, *values: Any) -> Iterator[None]:
    global _is_logging
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        thr = SLOW_OPERATION_THRESHOLD_MS
        if duration_ms > thr and not _is_logging and thr != float("inf"):
            _is_logging = True
            try:
                import traceback

                desc = _build_description(template, *values) + caller_frame(
                    "".join(traceback.format_stack(limit=12))
                )
                log_for_debugging(
                    f"[SLOW OPERATION DETECTED] {desc} ({duration_ms:.1f}ms)"
                )
            finally:
                _is_logging = False


def json_stringify(value: Any, *args: Any, **kwargs: Any) -> str:
    with slow_logging("JSON.stringify", value):
        return json.dumps(value, *args, **kwargs)  # type: ignore[arg-type]


def json_parse(text: str, **kwargs: Any) -> Any:
    with slow_logging("JSON.parse", text):
        if kwargs:
            return json.loads(text, **kwargs)
        return json.loads(text)


def clone_value(value: Any, memo: dict[int, Any] | None = None) -> Any:
    with slow_logging("structuredClone", value):
        return copy.deepcopy(value, memo)


def clone_deep(value: Any) -> Any:
    with slow_logging("cloneDeep", value):
        return copy.deepcopy(value)


clone = clone_value


def write_file_sync_deprecated(
    file_path: str, data: str | bytes, **options: Any
) -> None:
    with slow_logging("fs.writeFileSync", file_path, data):
        mode = "wb" if isinstance(data, bytes) else "w"
        enc = None if isinstance(data, bytes) else options.get("encoding", "utf-8")
        with open(file_path, mode, encoding=enc) as f:
            f.write(data)  # type: ignore[arg-type]
        if options.get("flush"):
            # best-effort sync
            import os

            fd = os.open(file_path, os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
