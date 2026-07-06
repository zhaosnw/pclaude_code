"""
Graceful process shutdown (hooks, cleanup, exit).

Port of: src/utils/gracefulShutdown.ts — without Ink/TUI; wire cleanup registry at app layer.
"""

from __future__ import annotations

import asyncio
import os
import sys
from functools import lru_cache
from typing import Any, Awaitable, Callable

from hare.utils.debug import log_for_debugging

ExitReason = str


def _run_cleanup_functions() -> Awaitable[None]:
    return asyncio.sleep(0)


def _shutdown_1p_logging() -> Awaitable[None]:
    return asyncio.sleep(0)


def _shutdown_datadog() -> Awaitable[None]:
    try:
        from hare.services.analytics.datadog import _shutdown_datadog as _dd_shutdown

        return _dd_shutdown()
    except ImportError:
        return asyncio.sleep(0)


shutdown_in_progress = False
_resume_hint_printed = False


def is_shutting_down() -> bool:
    return shutdown_in_progress


def reset_shutdown_state() -> None:
    global shutdown_in_progress, _resume_hint_printed
    shutdown_in_progress = False
    _resume_hint_printed = False


def get_pending_shutdown_for_testing() -> asyncio.Task | None:
    return None


def _cleanup_terminal_modes() -> None:
    if sys.stdout.isatty():
        pass


def _print_resume_hint() -> None:
    global _resume_hint_printed
    _resume_hint_printed = True


@lru_cache(maxsize=1)
def setup_graceful_shutdown() -> None:
    import signal

    def _schedule(_sig: int, _f: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(graceful_shutdown(0 if _sig == signal.SIGINT else 143))

    if os.name != "nt":
        try:
            signal.signal(signal.SIGINT, lambda s, f: _schedule(s, f))
            signal.signal(signal.SIGTERM, lambda s, f: _schedule(s, f))
        except OSError:
            pass


def graceful_shutdown_sync(
    exit_code: int = 0,
    reason: ExitReason = "other",
    options: dict[str, Any] | None = None,
) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(graceful_shutdown(exit_code, reason, options))
    else:
        asyncio.create_task(graceful_shutdown(exit_code, reason, options))


async def graceful_shutdown(
    exit_code: int = 0,
    reason: ExitReason = "other",
    options: dict[str, Any] | None = None,
) -> None:
    global shutdown_in_progress
    if shutdown_in_progress:
        return
    shutdown_in_progress = True
    _cleanup_terminal_modes()
    _print_resume_hint()
    try:
        await asyncio.wait_for(_run_cleanup_functions(), timeout=2.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        await asyncio.gather(
            asyncio.wait_for(_shutdown_1p_logging(), timeout=0.5),
            asyncio.wait_for(_shutdown_datadog(), timeout=0.5),
        )
    except Exception:  # noqa: BLE001
        pass
    final = (options or {}).get("final_message")
    if final:
        print(final, file=sys.stderr)
    log_for_debugging(f"graceful_shutdown reason={reason} code={exit_code}")
    raise SystemExit(exit_code)
