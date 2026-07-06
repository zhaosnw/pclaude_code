"""
Line-range file reader (fast path + streaming). Port of src/utils/readFileInRange.ts.
"""

from __future__ import annotations

import asyncio
import os
import stat as stat_mod
from dataclasses import dataclass

from hare.utils.format import format_bytes

FAST_PATH_MAX_SIZE = 10 * 1024 * 1024


@dataclass
class ReadFileRangeResult:
    content: str
    line_count: int
    total_lines: int
    total_bytes: int
    read_bytes: int
    mtime_ms: float
    truncated_by_bytes: bool | None = None


class FileTooLargeError(OSError):
    def __init__(self, size_in_bytes: int, max_size_bytes: int) -> None:
        self.size_in_bytes = size_in_bytes
        self.max_size_bytes = max_size_bytes
        super().__init__(
            f"File content ({format_bytes(size_in_bytes)}) exceeds maximum allowed size "
            f"({format_bytes(max_size_bytes)}). Use offset and limit parameters to read specific portions."
        )


def _read_fast(
    raw: str,
    mtime_ms: float,
    offset: int,
    max_lines: int | None,
    truncate_at_bytes: int | None,
) -> ReadFileRangeResult:
    end_line = offset + max_lines if max_lines is not None else 1_000_000_000
    if raw and ord(raw[0]) == 0xFEFF:
        raw = raw[1:]
    selected: list[str] = []
    line_index = 0
    start = 0
    selected_bytes = 0
    truncated_by_bytes = False
    total_bytes = len(raw.encode("utf-8"))

    def try_push(line: str) -> bool:
        nonlocal selected_bytes, truncated_by_bytes
        if truncate_at_bytes is not None:
            sep = 1 if selected else 0
            next_b = selected_bytes + sep + len(line.encode("utf-8"))
            if next_b > truncate_at_bytes:
                truncated_by_bytes = True
                return False
            selected_bytes = next_b
        selected.append(line)
        return True

    while True:
        nl = raw.find("\n", start)
        if nl == -1:
            break
        if offset <= line_index < end_line and not truncated_by_bytes:
            line = raw[start:nl]
            if line.endswith("\r"):
                line = line[:-1]
            if not try_push(line):
                pass
        line_index += 1
        start = nl + 1
    if offset <= line_index < end_line and not truncated_by_bytes:
        line = raw[start:]
        if line.endswith("\r"):
            line = line[:-1]
        try_push(line)
    line_index += 1
    content = "\n".join(selected)
    return ReadFileRangeResult(
        content=content,
        line_count=len(selected),
        total_lines=line_index,
        total_bytes=total_bytes,
        read_bytes=len(content.encode("utf-8")),
        mtime_ms=mtime_ms,
        truncated_by_bytes=truncated_by_bytes if truncate_at_bytes else None,
    )


async def read_file_in_range(
    file_path: str,
    offset: int = 0,
    max_lines: int | None = None,
    max_bytes: int | None = None,
    signal: asyncio.CancelledError | None = None,
    options: dict[str, bool] | None = None,
) -> ReadFileRangeResult:
    _ = signal
    opt = options or {}
    truncate_on_byte_limit = opt.get("truncateOnByteLimit", False)

    def stat_read() -> tuple[os.stat_result, str | None]:
        st = os.stat(file_path)
        if stat_mod.S_ISREG(st.st_mode) and st.st_size < FAST_PATH_MAX_SIZE:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                return st, f.read()
        return st, None

    st, text = await asyncio.to_thread(stat_read)
    if os.path.isdir(file_path):
        raise IsADirectoryError(
            f"EISDIR: illegal operation on a directory, read '{file_path}'"
        )
    if text is not None:
        if (
            not truncate_on_byte_limit
            and max_bytes is not None
            and st.st_size > max_bytes
        ):
            raise FileTooLargeError(st.st_size, max_bytes)
        truncate_at = max_bytes if truncate_on_byte_limit else None
        return _read_fast(text, st.st_mtime * 1000, offset, max_lines, truncate_at)
    return await asyncio.to_thread(
        _stream_read,
        file_path,
        offset,
        max_lines,
        max_bytes,
        truncate_on_byte_limit,
    )


def _stream_read(
    file_path: str,
    offset: int,
    max_lines: int | None,
    max_bytes: int | None,
    truncate_on_byte_limit: bool,
) -> ReadFileRangeResult:
    end_line = offset + max_lines if max_lines is not None else 1_000_000_000
    selected: list[str] = []
    current_index = 0
    partial = ""
    total_read = 0
    selected_bytes = 0
    truncated_by_bytes = False
    is_first = True
    mtime_ms = os.stat(file_path).st_mtime * 1000

    with open(file_path, encoding="utf-8", errors="replace") as stream:
        while True:
            chunk = stream.read(512 * 1024)
            if not chunk:
                break
            if is_first:
                is_first = False
                if chunk and ord(chunk[0]) == 0xFEFF:
                    chunk = chunk[1:]
            total_read += len(chunk.encode("utf-8"))
            if (
                not truncate_on_byte_limit
                and max_bytes is not None
                and total_read > max_bytes
            ):
                raise FileTooLargeError(total_read, max_bytes)
            data = partial + chunk
            partial = ""
            start = 0
            while True:
                nl = data.find("\n", start)
                if nl == -1:
                    if current_index >= offset and current_index < end_line:
                        partial = data[start:]
                    break
                if offset <= current_index < end_line:
                    line = data[start:nl]
                    if line.endswith("\r"):
                        line = line[:-1]
                    if truncate_on_byte_limit and max_bytes is not None:
                        sep = 1 if selected else 0
                        nb = selected_bytes + sep + len(line.encode("utf-8"))
                        if nb > max_bytes:
                            truncated_by_bytes = True
                            end_line = current_index
                            break
                        selected_bytes = nb
                    selected.append(line)
                current_index += 1
                start = nl + 1
            if truncated_by_bytes and end_line <= current_index:
                break

    if offset <= current_index < end_line and partial:
        line = partial
        if line.endswith("\r"):
            line = line[:-1]
        if truncate_on_byte_limit and max_bytes is not None:
            sep = 1 if selected else 0
            if selected_bytes + sep + len(line.encode("utf-8")) > max_bytes:
                truncated_by_bytes = True
            else:
                selected.append(line)
        else:
            selected.append(line)
        current_index += 1

    content = "\n".join(selected)
    return ReadFileRangeResult(
        content=content,
        line_count=len(selected),
        total_lines=current_index,
        total_bytes=total_read,
        read_bytes=len(content.encode("utf-8")),
        mtime_ms=mtime_ms,
        truncated_by_bytes=truncated_by_bytes if truncate_on_byte_limit else None,
    )
