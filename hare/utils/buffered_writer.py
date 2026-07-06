"""Buffered async writer with periodic flush (`bufferedWriter.ts`)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class WriteFn(Protocol):
    def __call__(self, content: str) -> None: ...


@dataclass
class BufferedWriter:
    write: Callable[[str], None]
    flush: Callable[[], None]
    dispose: Callable[[], None]


def create_buffered_writer(
    write_fn: WriteFn,
    flush_interval_ms: int = 1000,
    max_buffer_size: int = 100,
    max_buffer_bytes: float = float("inf"),
    immediate_mode: bool = False,
) -> BufferedWriter:
    buffer: list[str] = []
    buffer_bytes = 0
    flush_timer: threading.Timer | None = None
    pending_overflow: list[str] | None = None
    lock = threading.Lock()

    def clear_timer() -> None:
        nonlocal flush_timer
        if flush_timer:
            flush_timer.cancel()
            flush_timer = None

    def flush_impl() -> None:
        nonlocal buffer, buffer_bytes, pending_overflow
        with lock:
            if pending_overflow:
                write_fn("".join(pending_overflow))
                pending_overflow = None
            if not buffer:
                clear_timer()
                return
            write_fn("".join(buffer))
            buffer = []
            buffer_bytes = 0
            clear_timer()

    def schedule_flush() -> None:
        nonlocal flush_timer

        def _fire() -> None:
            flush_impl()

        clear_timer()
        flush_timer = threading.Timer(flush_interval_ms / 1000.0, _fire)
        flush_timer.daemon = True
        flush_timer.start()

    def flush_deferred() -> None:
        nonlocal buffer, buffer_bytes, pending_overflow
        with lock:
            if pending_overflow:
                pending_overflow.extend(buffer)
                buffer = []
                buffer_bytes = 0
                clear_timer()
                return
            detached = buffer
            buffer = []
            buffer_bytes = 0
            clear_timer()
            pending_overflow = detached

        def _run() -> None:
            nonlocal pending_overflow
            with lock:
                to_write = pending_overflow
                pending_overflow = None
            if to_write:
                write_fn("".join(to_write))

        threading.Thread(target=_run, daemon=True).start()

    def write(content: str) -> None:
        nonlocal buffer_bytes
        if immediate_mode:
            write_fn(content)
            return
        with lock:
            buffer.append(content)
            buffer_bytes += len(content)
        schedule_flush()
        with lock:
            if len(buffer) >= max_buffer_size or buffer_bytes >= max_buffer_bytes:
                flush_deferred()

    def dispose() -> None:
        flush_impl()

    return BufferedWriter(write=write, flush=flush_impl, dispose=dispose)
