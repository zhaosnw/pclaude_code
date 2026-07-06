"""
Find a needle in a file with line-bounded context window. Port of src/utils/readEditContext.ts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from hare.utils.errors import is_enoent

CHUNK_SIZE = 8 * 1024
MAX_SCAN_BYTES = 10 * 1024 * 1024


@dataclass
class EditContext:
    content: str
    line_offset: int
    truncated: bool


def _normalize(s: str) -> str:
    return s.replace("\r\n", "\n") if "\r" in s else s


def _scan_text(text: str, needle: str, context_lines: int) -> EditContext:
    if not needle:
        return EditContext(content="", line_offset=1, truncated=False)
    idx = text.find(needle)
    if idx == -1:
        alt = needle.replace("\n", "\r\n")
        idx = text.find(alt)
    if idx == -1:
        return EditContext(
            content="",
            line_offset=1,
            truncated=len(text.encode("utf-8")) >= MAX_SCAN_BYTES,
        )
    lines = text.split("\n")
    pos = 0
    match_line = 0
    for i, ln in enumerate(lines):
        seg = ln + ("\n" if i < len(lines) - 1 else "")
        if pos <= idx < pos + len(seg):
            match_line = i
            break
        pos += len(seg)
    start_line = max(0, match_line - context_lines)
    end_line = min(len(lines), match_line + context_lines + 1)
    chunk = "\n".join(lines[start_line:end_line])
    return EditContext(content=chunk, line_offset=start_line + 1, truncated=False)


async def read_edit_context(
    path: str, needle: str, context_lines: int = 3
) -> EditContext | None:
    try:

        def _read() -> bytes:
            with open(path, "rb") as f:
                return f.read(MAX_SCAN_BYTES)

        raw = await asyncio.to_thread(_read)
    except OSError as e:
        if is_enoent(e):
            return None
        raise
    truncated = len(raw) >= MAX_SCAN_BYTES
    text = _normalize(raw.decode("utf-8", errors="replace"))
    ctx = _scan_text(text, needle, context_lines)
    if truncated and ctx.content == "" and needle not in text:
        return EditContext(content="", line_offset=1, truncated=True)
    return EditContext(
        content=ctx.content,
        line_offset=ctx.line_offset,
        truncated=ctx.truncated or truncated,
    )


async def open_for_scan(path: str):
    try:
        return await asyncio.to_thread(open, path, "rb")
    except OSError as e:
        if is_enoent(e):
            return None
        raise


async def scan_for_context(handle, needle: str, context_lines: int) -> EditContext:
    raw = handle.read(MAX_SCAN_BYTES)
    text = _normalize(raw.decode("utf-8", errors="replace"))
    return _scan_text(text, needle, context_lines)


async def read_capped(handle) -> str | None:
    raw = handle.read(MAX_SCAN_BYTES + 1)
    if len(raw) > MAX_SCAN_BYTES:
        return None
    return _normalize(raw.decode("utf-8", errors="replace"))
