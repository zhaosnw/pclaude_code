"""
Heavy error log sink (JSONL files, MCP logs).

Port of: src/utils/errorLogSink.ts
"""

from __future__ import annotations

import json
import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hare.utils.debug import log_for_debugging


def _get_session_id() -> str:
    return ""


MACRO_VERSION = "2.1.88"


def _cache_errors_base() -> Path:
    return Path(os.environ.get("CLAUDE_CACHE_DIR", ".cache"))


def date_to_filename(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


_DATE = date_to_filename(datetime.now(timezone.utc))


def get_errors_path() -> str:
    return str(_cache_errors_base() / "errors" / f"{_DATE}.jsonl")


def get_mcp_logs_path(server_name: str) -> str:
    return str(_cache_errors_base() / "mcp_logs" / server_name / f"{_DATE}.jsonl")


@dataclass
class JsonlWriter:
    path: str

    def write(self, obj: object) -> None:
        line = json.dumps(obj, default=str) + "\n"
        p = Path(self.path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def flush(self) -> None:
        pass

    def dispose(self) -> None:
        pass


_log_writers: dict[str, JsonlWriter] = {}


def _get_log_writer(path: str) -> JsonlWriter:
    if path not in _log_writers:
        _log_writers[path] = JsonlWriter(path=path)
    return _log_writers[path]


def _append_to_log(path: str, message: dict[str, Any]) -> None:
    if os.environ.get("USER_TYPE") != "ant":
        return
    payload = {
        **message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cwd": os.getcwd(),
        "userType": os.environ.get("USER_TYPE"),
        "sessionId": _get_session_id(),
        "version": MACRO_VERSION,
    }
    _get_log_writer(path).write(payload)


def _log_error_impl(error: BaseException) -> None:
    err_s = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    log_for_debugging(f"{type(error).__name__}: {err_s}", level="error")
    _append_to_log(get_errors_path(), {"error": err_s})


def _log_mcp_error_impl(server_name: str, error: object) -> None:
    log_for_debugging(f'MCP server "{server_name}" {error}', level="error")
    err_str = (
        "".join(traceback.format_exception(type(error), error, error.__traceback__))
        if isinstance(error, BaseException)
        else str(error)
    )
    _get_log_writer(get_mcp_logs_path(server_name)).write(
        {
            "error": err_str,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sessionId": _get_session_id(),
            "cwd": os.getcwd(),
        }
    )


def _log_mcp_debug_impl(server_name: str, message: str) -> None:
    log_for_debugging(f'MCP server "{server_name}": {message}')
    _get_log_writer(get_mcp_logs_path(server_name)).write(
        {
            "debug": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sessionId": _get_session_id(),
            "cwd": os.getcwd(),
        }
    )


_attach_error_log_sink: Callable[[Any], None] | None = None


def set_error_log_sink_attacher(fn: Callable[[Any], None]) -> None:
    global _attach_error_log_sink
    _attach_error_log_sink = fn


def initialize_error_log_sink() -> None:
    if _attach_error_log_sink is None:
        return
    _attach_error_log_sink(
        {
            "log_error": _log_error_impl,
            "log_mcp_error": _log_mcp_error_impl,
            "log_mcp_debug": _log_mcp_debug_impl,
            "get_errors_path": get_errors_path,
            "get_mcp_logs_path": get_mcp_logs_path,
        }
    )
    log_for_debugging("Error log sink initialized")
