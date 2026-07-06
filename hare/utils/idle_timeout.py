"""SDK idle timeout — port of `idleTimeout.ts`."""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from hare.utils.debug import log_for_debugging
from hare.utils.graceful_shutdown import graceful_shutdown_sync


def create_idle_timeout_manager(
    is_idle: Callable[[], bool],
) -> tuple[Callable[[], None], Callable[[], None]]:
    raw = os.environ.get("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY")
    delay_ms: int | None = None
    if raw:
        try:
            v = int(raw, 10)
            if v > 0:
                delay_ms = v
        except ValueError:
            pass
    is_valid = delay_ms is not None
    timer_holder: list[threading.Timer | None] = [None]
    last_idle: list[float] = [0.0]

    def start() -> None:
        t = timer_holder[0]
        if t is not None:
            t.cancel()
            timer_holder[0] = None
        if not is_valid or delay_ms is None:
            return
        last_idle[0] = time.time()

        def _fire() -> None:
            idle_duration = (time.time() - last_idle[0]) * 1000
            if is_idle() and idle_duration >= delay_ms:
                log_for_debugging(f"Exiting after {delay_ms}ms of idle time")
                graceful_shutdown_sync(0)

        timer_holder[0] = threading.Timer(delay_ms / 1000.0, _fire)
        timer_holder[0].start()

    def stop() -> None:
        t = timer_holder[0]
        if t is not None:
            t.cancel()
            timer_holder[0] = None

    return start, stop
