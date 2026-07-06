"""Classify paths targeting memdir / session storage — port of `memoryFileDetection.ts` (stubs)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.windows_paths import (
    posix_path_to_windows_path,
    windows_path_to_posix_path,
)

SessionFileType = Literal["session_memory", "session_transcript"] | None
MemoryScope = Literal["personal", "team"]

IS_WINDOWS = os.name == "nt"


def _to_posix(p: str) -> str:
    return p.replace("\\", "/")


def _to_comparable(p: str) -> str:
    x = _to_posix(p)
    return x.lower() if IS_WINDOWS else x


def detect_session_file_type(file_path: str) -> SessionFileType:
    cfg = _to_comparable(get_hare_config_home_dir())
    n = _to_comparable(file_path)
    if not n.startswith(cfg):
        return None
    if "/session-memory/" in n and n.endswith(".md"):
        return "session_memory"
    if "/projects/" in n and n.endswith(".jsonl"):
        return "session_transcript"
    return None


def detect_session_pattern_type(pattern: str) -> SessionFileType:
    n = pattern.replace("\\", "/")
    if "session-memory" in n and (".md" in n or n.endswith("*")):
        return "session_memory"
    if ".jsonl" in n or ("projects" in n and "*.jsonl" in n):
        return "session_transcript"
    return None


def is_auto_mem_file(_file_path: str) -> bool:
    try:
        from hare.memdir.paths import is_auto_mem_path, is_auto_memory_enabled  # type: ignore[import-not-found]

        return is_auto_memory_enabled() and is_auto_mem_path(_file_path)
    except ImportError:
        return False


def memory_scope_for_path(file_path: str) -> MemoryScope | None:
    try:
        from hare.memdir.team_mem_paths import is_team_mem_file  # type: ignore[import-not-found]
    except ImportError:

        def is_team_mem_file(_p: str) -> bool:
            return False

    if is_team_mem_file(file_path):
        return "team"
    if is_auto_mem_file(file_path):
        return "personal"
    return None


def is_auto_managed_memory_file(file_path: str) -> bool:
    if is_auto_mem_file(file_path):
        return True
    try:
        from hare.memdir.team_mem_paths import is_team_mem_file  # type: ignore[import-not-found]
    except ImportError:

        def is_team_mem_file(_p: str) -> bool:
            return False

    if is_team_mem_file(file_path):
        return True
    if detect_session_file_type(file_path):
        return True
    try:
        from hare.tools.agent_tool.agent_memory import is_agent_memory_path  # type: ignore[import-not-found]
    except ImportError:

        def is_agent_memory_path(_p: str) -> bool:
            return False

    return is_agent_memory_path(file_path)


def is_memory_directory(dir_path: str) -> bool:
    p = Path(dir_path).resolve()
    n = _to_comparable(str(p))
    cfg = _to_comparable(get_hare_config_home_dir())
    if "/agent-memory/" in n or "/agent-memory-local/" in n:
        return True
    if n.startswith(cfg) or "session-memory" in n:
        return True
    return False


def is_shell_command_targeting_memory(command: str) -> bool:
    cfg = get_hare_config_home_dir()
    cmd_c = _to_comparable(command)
    dirs = [cfg]
    if not any(_to_comparable(d) in cmd_c for d in dirs):
        if IS_WINDOWS and any(
            windows_path_to_posix_path(d).lower() in cmd_c for d in dirs
        ):
            pass
        else:
            return False
    matches = re.findall(r"(?:[A-Za-z]:[/\\]|/)[^\s'\"]+", command)
    if not matches:
        return False
    for m in matches:
        clean = re.sub(r"[,;|&>]+$", "", m)
        native = posix_path_to_windows_path(clean) if IS_WINDOWS else clean
        if is_auto_managed_memory_file(native) or is_memory_directory(native):
            return True
    return False


def is_auto_managed_memory_pattern(pattern: str) -> bool:
    if detect_session_pattern_type(pattern):
        return True
    x = pattern.replace("\\", "/")
    return "agent-memory/" in x or "agent-memory-local/" in x
