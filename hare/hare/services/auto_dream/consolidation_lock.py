"""
Cross-process lock for consolidation runs.

Port of: src/services/autoDream/consolidationLock.ts (141 lines)

Lock file whose mtime IS lastConsolidatedAt. Body is the holder's PID.
Lives inside the memory dir (getAutoMemPath) so it keys on git-root
like memory does, and so it's writable even when the memory path comes
from an env/settings override whose parent may not be.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from hare.utils.debug import log_for_debugging
from hare.utils.generic_process_utils import is_process_running

# ---------------------------------------------------------------------------
# Constants (TS consolidationLock.ts L17-19)
# ---------------------------------------------------------------------------

LOCK_FILE = ".consolidate-lock"

# Stale past this even if the PID is live (PID reuse guard).
HOLDER_STALE_MS = 60 * 60 * 1000  # 1 hour

# Module-level async lock for in-process mutual exclusion
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _lock_path() -> str:
    """Resolve the full path to the lock file inside the auto-memory directory."""
    from hare.memdir.paths import get_auto_mem_path

    return str(Path(get_auto_mem_path()) / LOCK_FILE)


# ---------------------------------------------------------------------------
# In-process async lock (context manager)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def consolidation_lock() -> AsyncIterator[None]:
    """In-process async mutual exclusion for consolidation runs."""
    async with _lock:
        yield


# ---------------------------------------------------------------------------
# Read last consolidated time (TS L25-35)
# ---------------------------------------------------------------------------


async def read_last_consolidated_at() -> float:
    """mtime of the lock file = lastConsolidatedAt. 0 if absent.

    Per-turn cost: one stat.
    """
    try:
        s = os.stat(_lock_path())
        return s.st_mtime * 1000.0  # epoch ms
    except OSError:
        return 0.0


# ---------------------------------------------------------------------------
# Acquire (TS L37-83)
# ---------------------------------------------------------------------------


async def try_acquire_consolidation_lock() -> float | None:
    """Acquire: write PID -> mtime = now. Returns the pre-acquire mtime
    (for rollback), or None if blocked / lost a race.

    Success -> do nothing. mtime stays at now.
    Failure -> rollback_consolidation_lock(priorMtime) rewinds mtime.
    Crash   -> mtime stuck, dead PID -> next process reclaims.
    """
    path = _lock_path()
    now_ms = time.time() * 1000.0

    mtime_ms: float | None = None
    holder_pid: int | None = None

    try:
        s = os.stat(path)
        mtime_ms = s.st_mtime * 1000.0

        # Read PID body
        try:
            raw = await _read_file_async(path)
            parsed = int(raw.strip())
            holder_pid = parsed if parsed > 0 else None
        except (ValueError, OSError):
            holder_pid = None
    except OSError:
        # ENOENT — no prior lock
        pass

    # Check if still held by a live process
    if mtime_ms is not None and (now_ms - mtime_ms) < HOLDER_STALE_MS:
        if holder_pid is not None and is_process_running(holder_pid):
            log_for_debugging(
                f"[autoDream] lock held by live PID {holder_pid} "
                f"(mtime {round((now_ms - mtime_ms) / 1000)}s ago)",
            )
            return None
        # Dead PID or unparseable body — reclaim.

    # Memory dir may not exist yet.
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Write our PID
    await _write_file_async(path, str(os.getpid()))

    # Two reclaimers both write -> last wins the PID. Loser bails on re-read.
    try:
        verify = await _read_file_async(path)
    except OSError:
        return None

    try:
        if int(verify.strip()) != os.getpid():
            return None
    except ValueError:
        return None

    return mtime_ms if mtime_ms is not None else 0.0


# ---------------------------------------------------------------------------
# Rollback (TS L86-108)
# ---------------------------------------------------------------------------


async def rollback_consolidation_lock(prior_mtime: float) -> None:
    """Rewind mtime to pre-acquire after a failed fork. Clears the PID body —
    otherwise our still-running process would look like it's holding.
    priorMtime 0 -> unlink (restore no-file).
    """
    path = _lock_path()
    try:
        if prior_mtime == 0.0:
            os.unlink(path)
            return
        await _write_file_async(path, "")
        t = prior_mtime / 1000.0  # utimes wants seconds
        os.utime(path, (t, t))
    except OSError as e:
        log_for_debugging(
            f"[autoDream] rollback failed: {e} — next trigger delayed to minHours",
        )


# ---------------------------------------------------------------------------
# List sessions touched since (TS L110-124)
# ---------------------------------------------------------------------------


async def list_sessions_touched_since(since_ms: float) -> list[str]:
    """Session IDs with mtime after sinceMs.

    Uses mtime (sessions TOUCHED since), not birthtime (0 on ext4).
    Caller excludes the current session. Scans per-cwd transcripts — it's
    a skip-gate, so undercounting worktree sessions is safe.
    """
    from hare.bootstrap.state import get_original_cwd
    from hare.utils.session_storage import get_project_dir
    from hare.utils.list_sessions_impl import list_candidates

    dir_path = get_project_dir(get_original_cwd())
    try:
        candidates = await list_candidates(dir_path, True)
    except Exception:
        return []

    return [c.session_id for c in candidates if c.mtime > since_ms]


# ---------------------------------------------------------------------------
# Record consolidation (TS L127-140)
# ---------------------------------------------------------------------------


async def record_consolidation() -> None:
    """Stamp from manual /dream. Optimistic — fires at prompt-build time,
    no post-skill completion hook. Best-effort.
    """
    try:
        os.makedirs(os.path.dirname(_lock_path()), exist_ok=True)
        await _write_file_async(_lock_path(), str(os.getpid()))
    except OSError as e:
        log_for_debugging(
            f"[autoDream] recordConsolidation write failed: {e}",
        )


# ---------------------------------------------------------------------------
# Async file I/O helpers
# ---------------------------------------------------------------------------


async def _read_file_async(path: str) -> str:
    """Read a (small) file asynchronously via a thread."""

    def _read() -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    return await asyncio.to_thread(_read)


async def _write_file_async(path: str, content: str) -> None:
    """Write a (small) file asynchronously via a thread."""

    def _write() -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    await asyncio.to_thread(_write)
