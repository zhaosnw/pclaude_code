"""
Streaming task stdout/stderr (file or pipe mode).

Port of: src/utils/task/TaskOutput.ts
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Callable, Deque


ProgressCallback = Callable[..., None]


class TaskOutput:
    """Buffers or tails on-disk output for a shell task."""

    DEFAULT_MAX_MEMORY = 8 * 1024 * 1024

    def __init__(
        self,
        task_id: str,
        on_progress: ProgressCallback | None,
        stdout_to_file: bool = False,
        max_memory: int | None = None,
        output_path: Path | None = None,
    ) -> None:
        self.task_id = task_id
        self.stdout_to_file = stdout_to_file
        self._max_memory = max_memory or self.DEFAULT_MAX_MEMORY
        self._on_progress = on_progress
        self.path = str(output_path) if output_path else f"/tmp/hare-task-{task_id}.log"
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._recent_lines: Deque[str] = deque(maxlen=1000)
        self._total_lines = 0
        self._total_bytes = 0

    async def get_stdout(self) -> str:
        if self.stdout_to_file and Path(self.path).is_file():
            return Path(self.path).read_text(encoding="utf-8", errors="replace")
        return self._stdout_buffer

    def get_stderr(self) -> str:
        return "" if self.stdout_to_file else self._stderr_buffer

    def write_stdout(self, chunk: str) -> None:
        self._stdout_buffer += chunk
        self._record_line(chunk)

    def write_stderr(self, chunk: str) -> None:
        self._stderr_buffer += chunk

    def _record_line(self, chunk: str) -> None:
        self._total_bytes += len(chunk.encode("utf-8", errors="replace"))
        for line in chunk.splitlines():
            self._recent_lines.append(line)
            self._total_lines += 1
        if self._on_progress:
            self._on_progress(
                "\n".join(list(self._recent_lines)[-5:]),
                self._stdout_buffer,
                self._total_lines,
                self._total_bytes,
                True,
            )

    async def tail_progress(self) -> None:
        await asyncio.sleep(0)
