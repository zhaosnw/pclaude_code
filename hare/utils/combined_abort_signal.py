"""Combine AbortSignals + optional timeout (`combinedAbortSignal.ts`)."""

from __future__ import annotations

import threading
from typing import Callable

from hare.utils.abort_controller import AbortSignal, create_abort_controller


def create_combined_abort_signal(
    signal: AbortSignal | None,
    *,
    signal_b: AbortSignal | None = None,
    timeout_ms: int | None = None,
) -> tuple[AbortSignal, Callable[[], None]]:
    """Return a new signal that aborts when any input aborts or timeout elapses."""
    ctrl = create_abort_controller()

    if (signal and signal.aborted) or (signal_b and signal_b.aborted):
        ctrl.abort()
        return ctrl.signal, lambda: None

    timer: threading.Timer | None = None

    def abort_combined() -> None:
        nonlocal timer
        if timer is not None:
            timer.cancel()
            timer = None
        ctrl.abort()

    if timeout_ms is not None:
        timer = threading.Timer(timeout_ms / 1000.0, abort_combined)
        timer.daemon = True
        timer.start()

    def listener() -> None:
        abort_combined()

    if signal:
        signal.add_event_listener("abort", listener)
    if signal_b:
        signal_b.add_event_listener("abort", listener)

    def cleanup() -> None:
        nonlocal timer
        if timer is not None:
            timer.cancel()
            timer = None
        if signal:
            signal.remove_event_listener("abort", listener)
        if signal_b:
            signal_b.remove_event_listener("abort", listener)

    return ctrl.signal, cleanup
