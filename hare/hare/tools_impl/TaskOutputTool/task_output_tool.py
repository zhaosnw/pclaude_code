"""TaskOutput Tool — retrieve completed output, logs, or current progress for a background task.

Port of: src/tools/TaskOutputTool/TaskOutputTool.tsx

Resolution order:
  1. In-memory task registry (TaskCreateTool state): completed/failed/running/pending.
  2. Disk-persisted output (task_output.json via load_task_output).
  3. Raw on-disk log files (shell-task stdout/stderr from TaskOutput class).
  4. Not-found error when no source has output for the given task_id.

The tool returns structured dicts with `task_id`, `status`, `content`, `is_error`,
and `source` (one of "memory", "disk", "disk_log").
"""

from __future__ import annotations

import json
import os
from typing import Any

from hare.tools_impl.TaskTools.task_create_tool import get_task
from hare.utils.task.disk_output import load_task_output

TASK_OUTPUT_TOOL_NAME = "TaskOutput"
MAX_OUTPUT_CHARS = 200_000


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID whose output to retrieve.",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum lines of output to return (default: all). Useful for tailing running tasks.",
            },
            "block": {
                "type": "boolean",
                "description": "Wait for the task to finish before returning (up to timeout).",
            },
            "timeout": {
                "type": "integer",
                "description": "Max milliseconds to wait when block=true (default: 60000).",
            },
        },
        "required": ["task_id"],
    }


async def call(
    task_id: str,
    max_lines: int = 0,
    block: bool = False,
    timeout: int = 60_000,
    **ctx: Any,
) -> dict[str, Any]:
    """Retrieve output for a background task — in-memory result, disk, or log file.

    Returns a structured dict: { type, task_id, status, content, is_error, source }.
    """
    if not task_id or not isinstance(task_id, str) or not task_id.strip():
        return {
            "type": "error",
            "task_id": str(task_id),
            "status": "invalid",
            "content": "task_id is required and must be a non-empty string.",
            "is_error": True,
        }

    # ---- Step 0: optionally block until the task finishes ---------------
    if block:
        import asyncio
        import time as _time

        deadline = _time.time() + max(0.0, timeout / 1000)
        while _time.time() < deadline:
            t = get_task(task_id)
            if t is None or getattr(t, "status", None) in (
                "completed", "failed", "success", "error", "stopped"
            ):
                break
            await asyncio.sleep(0.1)

    # ---- Step 1: in-memory task lookup ----------------------------------
    task = get_task(task_id)
    if task is not None:
        return _from_memory(task, task_id, max_lines)

    # ---- Step 2: disk-only lookup (task not in memory) ------------------
    return _from_disk(task_id, max_lines)


# ------------------------------------------------------------------ helpers


def _from_memory(task: Any, task_id: str, max_lines: int) -> dict[str, Any]:
    """Build response from the in-memory TaskState."""
    status = task.status

    if status == "completed":
        output = _gather_completed_output(task, max_lines)
        return {
            "type": "tool_result",
            "task_id": task_id,
            "status": "completed",
            "content": output,
            "is_error": False,
            "source": "memory",
        }

    if status == "failed":
        error_msg = task.error or "Task failed with no error details."
        if task.result:
            error_msg = f"{error_msg}\nLast output: {task.result}"
        return {
            "type": "tool_result",
            "task_id": task_id,
            "status": "failed",
            "content": error_msg,
            "is_error": True,
            "source": "memory",
        }

    if status == "cancelled":
        partial = task.result or "(no partial output captured)"
        return {
            "type": "tool_result",
            "task_id": task_id,
            "status": "cancelled",
            "content": f"Task was cancelled.\nPartial output: {partial}",
            "is_error": False,
            "source": "memory",
        }

    # pending / running / unknown
    interim = task.result or "(task has not produced output yet)"
    header = f"[Task is {status}]"
    if getattr(task, "description", ""):
        header += f" {task.description}"
    return {
        "type": "tool_result",
        "task_id": task_id,
        "status": status,
        "content": f"{header}\n{_tail(str(interim), max_lines)}",
        "is_error": False,
        "source": "memory",
    }


def _from_disk(task_id: str, max_lines: int) -> dict[str, Any]:
    """Fallback: search disk-persisted output when the task is not in memory."""

    # Structured disk output (JSON)
    disk = load_task_output(task_id)
    if disk and isinstance(disk, dict):
        content = disk.get("content") or disk.get("result") or json.dumps(disk, indent=2)
        status = disk.get("status", "completed")
        return {
            "type": "tool_result",
            "task_id": task_id,
            "status": status,
            "content": _tail(str(content), max_lines),
            "is_error": status == "failed",
            "source": "disk",
        }

    # Raw shell-task log (TaskOutput on-disk path)
    log_path = f"/tmp/hare-task-{task_id}.log"
    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            return {
                "type": "tool_result",
                "task_id": task_id,
                "status": "completed",
                "content": _tail(raw, max_lines),
                "is_error": False,
                "source": "disk_log",
            }
        except OSError:
            pass

    return {
        "type": "error",
        "task_id": task_id,
        "status": "not_found",
        "content": (
            f"No output found for task '{task_id}'. "
            "The task may have expired, never existed, or its output was cleaned up."
        ),
        "is_error": True,
    }


def _gather_completed_output(task: Any, max_lines: int) -> str:
    """Best-effort output for a completed task: memory → disk JSON → log file."""
    # 1. In-memory result (set by _execute_task or TaskUpdate)
    if task.result:
        return _tail(str(task.result), max_lines)

    # 2. Disk output
    disk = load_task_output(task.task_id)
    if disk and isinstance(disk, dict):
        content = disk.get("content") or disk.get("result") or json.dumps(disk, indent=2)
        return _tail(str(content), max_lines)

    # 3. Raw shell-task log
    log_path = f"/tmp/hare-task-{task.task_id}.log"
    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            return _tail(raw, max_lines)
        except OSError:
            pass

    return "(task completed — no output captured)"


def _tail(text: str, max_lines: int) -> str:
    """Truncate text to the last `max_lines` lines, with an overall char cap."""
    truncated = text
    if max_lines > 0:
        lines = truncated.splitlines()
        if len(lines) > max_lines:
            truncated = (
                f"... (showing last {max_lines} of {len(lines)} lines)\n"
                + "\n".join(lines[-max_lines:])
            )
    if len(truncated) > MAX_OUTPUT_CHARS:
        truncated = truncated[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
    return truncated
