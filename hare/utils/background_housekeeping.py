"""Deferred background maintenance on session start (`backgroundHousekeeping.ts`)."""

from __future__ import annotations

import asyncio
import os
import threading

RECURRING_CLEANUP_INTERVAL_MS = 24 * 60 * 60 * 1000
DELAY_VERY_SLOW_OPS_MS = 10 * 60 * 1000


def start_background_housekeeping() -> None:
    """Schedule optional cleanups; heavy integrations are stubbed."""

    def run_slow() -> None:
        try:

            async def _go() -> None:
                from hare.utils.cleanup import cleanup_old_message_files_in_background

                await cleanup_old_message_files_in_background()

            asyncio.run(_go())
        except Exception:
            pass

    t = threading.Timer(DELAY_VERY_SLOW_OPS_MS / 1000.0, run_slow)
    t.daemon = True
    t.start()

    # Initialize auto-dream on startup (TS backgroundHousekeeping.ts L108-109)
    try:
        from hare.services.auto_dream.auto_dream import init_auto_dream

        init_auto_dream()
    except Exception:
        pass

    if os.environ.get("USER_TYPE") == "ant":

        def recurring() -> None:
            pass

        threading.Timer(RECURRING_CLEANUP_INTERVAL_MS / 1000.0, recurring).start()
