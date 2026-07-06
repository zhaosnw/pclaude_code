"""
Portable session JSONL helpers — subset port of `sessionStoragePortable.ts`.
Used by `list_sessions_impl.py`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.hash import djb2_hash

LITE_READ_BUF_SIZE = 65536
MAX_SANITIZED_LENGTH = 200

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def validate_uuid(maybe_uuid: Any) -> str | None:
    if not isinstance(maybe_uuid, str):
        return None
    return maybe_uuid if _UUID_RE.match(maybe_uuid) else None


def unescape_json_string(raw: str) -> str:
    if "\\" not in raw:
        return raw
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw


def extract_json_string_field(text: str, key: str) -> str | None:
    for pattern in (f'"{key}":"', f'"{key}": "'):
        idx = text.find(pattern)
        if idx < 0:
            continue
        i = idx + len(pattern)
        while i < len(text):
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == '"':
                return unescape_json_string(text[idx + len(pattern) : i])
            i += 1
    return None


def extract_last_json_string_field(text: str, key: str) -> str | None:
    patterns = (f'"{key}":"', f'"{key}": "')
    last_val: str | None = None
    for pattern in patterns:
        search_from = 0
        while True:
            idx = text.find(pattern, search_from)
            if idx < 0:
                break
            value_start = idx + len(pattern)
            i = value_start
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    last_val = unescape_json_string(text[value_start:i])
                    break
                i += 1
            search_from = i + 1 if i < len(text) else len(text)
    return last_val


_SKIP_FIRST = re.compile(
    r"^(?:\s*<[a-z][\w-]*[\s>]|\[Request interrupted by user[^\]]*\])"
)
_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>")
_BASH_INPUT_RE = re.compile(r"<bash-input>([\s\S]*?)</bash-input>")


def extract_first_prompt_from_head(head: str) -> str:
    start = 0
    command_fallback = ""
    while start < len(head):
        nl = head.find("\n", start)
        line = head[start:nl] if nl >= 0 else head[start:]
        start = nl + 1 if nl >= 0 else len(head)
        if '"type":"user"' not in line and '"type": "user"' not in line:
            continue
        if "tool_result" in line:
            continue
        if '"isMeta":true' in line or '"isMeta": true' in line:
            continue
        if '"isCompactSummary":true' in line or '"isCompactSummary": true' in line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") != "user":
                continue
            message = entry.get("message") or {}
            content = message.get("content")
            texts: list[str] = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                    ):
                        texts.append(block["text"])
            for raw in texts:
                result = " ".join(raw.split("\n")).strip()
                if not result:
                    continue
                cmd_match = _COMMAND_NAME_RE.search(result)
                if cmd_match:
                    if not command_fallback:
                        command_fallback = cmd_match.group(1) or ""
                    continue
                bash_match = _BASH_INPUT_RE.search(raw)
                if bash_match:
                    return f"! {bash_match.group(1).strip()}"
                if _SKIP_FIRST.match(result):
                    continue
                if len(result) > 200:
                    result = result[:200].strip() + "\u2026"
                return result
        except json.JSONDecodeError:
            continue
    return command_fallback


@dataclass
class LiteSessionFile:
    mtime: float
    size: int
    head: str
    tail: str


def read_session_lite(file_path: str | Path) -> LiteSessionFile | None:
    p = Path(file_path)
    try:
        st = p.stat()
        size_b = st.st_size
        mtime = st.st_mtime
    except OSError:
        return None
    buf = bytearray(LITE_READ_BUF_SIZE)
    try:
        with open(p, "rb") as fh:
            n = fh.readinto(buf)
            if n == 0:
                return None
            head = bytes(buf[:n]).decode("utf-8", errors="replace")
            tail_off = max(0, size_b - LITE_READ_BUF_SIZE)
            tail = head
            if tail_off > 0:
                fh.seek(tail_off)
                n2 = fh.readinto(buf)
                tail = bytes(buf[:n2]).decode("utf-8", errors="replace")
        # Match Node `mtime.getTime()` — milliseconds since epoch
        return LiteSessionFile(mtime=mtime * 1000.0, size=size_b, head=head, tail=tail)
    except OSError:
        return None


def simple_hash(s: str) -> str:
    return format(abs(djb2_hash(s)), "x")


def sanitize_path(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", name)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return sanitized
    h = simple_hash(name)
    return f"{sanitized[:MAX_SANITIZED_LENGTH]}-{h}"


def get_projects_dir() -> str:
    return str(Path(get_hare_config_home_dir()) / "projects")


def get_project_dir(project_dir: str) -> str:
    return str(Path(get_projects_dir()) / sanitize_path(project_dir))


async def canonicalize_path(dir_path: str) -> str:
    try:
        return str(Path(dir_path).resolve().as_posix())
    except OSError:
        return os.path.normpath(dir_path)


async def find_project_dir(project_path: str) -> str | None:
    exact = get_project_dir(project_path)
    p = Path(exact)
    try:
        if p.is_dir():
            return exact
    except OSError:
        pass
    sanitized = sanitize_path(project_path)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return None
    prefix = sanitized[:MAX_SANITIZED_LENGTH]
    projects = Path(get_projects_dir())
    try:
        for d in projects.iterdir():
            if d.is_dir() and d.name.startswith(prefix + "-"):
                return str(d)
    except OSError:
        pass
    return None
