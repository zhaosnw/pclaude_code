"""Persist large tool results to disk (port of toolResultStorage.ts)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, TypedDict

from hare.bootstrap import state as bootstrap_state
from hare.utils.slow_operations import json_stringify

TOOL_RESULTS_SUBDIR = "tool-results"
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
TOOL_RESULT_CLEARED_MESSAGE = "[Old tool result content cleared]"
PREVIEW_SIZE_BYTES = 2000
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000


class ContentReplacementRecord(TypedDict, total=False):
    session_id: str
    tool_use_id: str
    content: str


def get_persistence_threshold(
    tool_name: str, declared_max_result_size_chars: float
) -> float:
    _ = tool_name
    if not math.isfinite(declared_max_result_size_chars):
        return declared_max_result_size_chars
    return min(
        float(declared_max_result_size_chars), float(DEFAULT_MAX_RESULT_SIZE_CHARS)
    )


def get_tool_results_dir() -> str:
    cwd = bootstrap_state.get_original_cwd()
    project = Path(cwd).resolve()
    return str(project / bootstrap_state.get_session_id() / TOOL_RESULTS_SUBDIR)


def get_tool_result_path(tool_use_id: str, is_json: bool) -> str:
    ext = "json" if is_json else "txt"
    return str(Path(get_tool_results_dir()) / f"{tool_use_id}.{ext}")


async def ensure_tool_results_dir() -> None:
    Path(get_tool_results_dir()).mkdir(parents=True, exist_ok=True)


async def persist_tool_result(content: Any, tool_use_id: str) -> dict[str, Any]:
    await ensure_tool_results_dir()
    is_json = isinstance(content, list)
    path = get_tool_result_path(tool_use_id, is_json)
    text = json_stringify(content) if is_json else str(content)
    Path(path).write_text(text, encoding="utf-8")
    return {
        "filepath": path,
        "original_size": len(text),
        "is_json": is_json,
        "preview": text[:PREVIEW_SIZE_BYTES],
        "has_more": len(text) > PREVIEW_SIZE_BYTES,
    }
