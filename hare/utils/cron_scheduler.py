"""Cron scheduler core (`cronScheduler.ts`).

Manages a background tick loop that reads scheduled tasks from durable storage
(``.hare/scheduled_tasks.json``) and in-memory session state, computes jittered
next-fire times, and invokes an ``on_fire`` callback when tasks are due.

Lifecycle
---------
1. ``create_cron_scheduler(options)`` builds a ``CronScheduler`` with all
   dependencies wired in (lock, task storage, jitter config).
2. ``scheduler.start()`` begins the async tick loop.  The caller must be
   running inside an asyncio event loop.
3. On each tick the scheduler:
   a. Reloads durable tasks from disk at a configurable interval.
   b. Merges in-memory session-only tasks.
   c. Computes jittered next-fire times via ``cron_jitter_config``.
   d. Fires due tasks through ``on_fire``.
   e. Cleans up one-shot tasks and ages out expired recurring tasks.
4. ``scheduler.stop()`` terminates the tick loop and releases the lock.

Public API
----------
- ``scheduler.start()``          — begin the background scheduling loop
- ``scheduler.stop()``           — halt the loop and release the lock
- ``scheduler.is_running()``     — whether the tick loop is active
- ``scheduler.get_next_fire_time()`` — earliest upcoming fire (epoch ms), or None
- ``scheduler.list_tasks()``     — all currently tracked tasks
- ``scheduler.get_status()``     — dictionary of scheduler health/state
- ``await scheduler.add_task(task, session_only=False)``  — add a task live
- ``await scheduler.remove_task(task_id)``                 — remove a task live
- ``await scheduler.force_reload()``                       — reload tasks now

Companion modules
-----------------
- ``cron.py``            — cron-expression parsing, formatting, next-run calc
- ``cron_tasks.py``      — durable task CRUD on ``.hare/scheduled_tasks.json``
- ``cron_tasks_lock.py`` — file-based scheduler-exclusivity lock
- ``cron_jitter_config.py`` — GrowthBook-backed jitter configuration
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from hare.utils.cron import cron_to_human, next_cron_run_ms, parse_cron_expression
from hare.utils.cron_jitter_config import (
    CronJitterConfig,
    compute_one_shot_jittered_fire_time_ms,
    compute_recurring_jitter_ms,
    get_cron_jitter_config,
)
from hare.utils.cron_tasks import (
    CronTask,
    find_missed_tasks,
    read_cron_tasks,
    remove_cron_tasks,
    write_cron_tasks,
)
from hare.utils.cron_tasks_lock import (
    release_scheduler_lock,
    try_acquire_scheduler_lock,
)
from hare.utils.debug import log_for_debugging

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How often to re-read the durable tasks file (ms).
# Kept relatively short so one process adding a task is picked up by another
# process holding the scheduler, but long enough to avoid thrashing the disk.
DEFAULT_RELOAD_INTERVAL_MS = 5_000

# How often the scheduler wakes up to check for due tasks (ms).
# One second is the sweet spot: responsive enough for human-scale scheduling
# (users won't notice 1 s of jitter on top of the intentional jitter) without
# burning CPU.
DEFAULT_TICK_INTERVAL_MS = 1_000

# Maximum age for recurring non-permanent tasks (ms).  Tasks older than this
# are automatically removed. Mirrors DEFAULT_CRON_JITTER_CONFIG["recurringMaxAgeMs"].
DEFAULT_RECURRING_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000  # 7 days

# Maximum number of consecutive on_fire failures before a task is skipped.
# Prevents a broken callback from burning CPU retrying every tick.
MAX_CONSECUTIVE_FAILURES = 3

# How long the scheduler waits (seconds) for in-flight fire callbacks to
# complete during shutdown before force-cancelling.
SHUTDOWN_GRACE_PERIOD_S = 5.0


# ---------------------------------------------------------------------------
# Helpers (preserved from original stub)
# ---------------------------------------------------------------------------


def is_recurring_task_aged(t: CronTask, now_ms: float, max_age_ms: float) -> bool:
    """Return True if *t* is a recurring non-permanent task past its max age."""
    if max_age_ms == 0:
        return False
    return bool(t.recurring and not t.permanent and now_ms - t.created_at >= max_age_ms)


def build_missed_task_notification(missed: list[CronTask]) -> str:
    """Build a human-readable notification about one-shot tasks missed while
    Hare was not running."""
    plural = len(missed) > 1
    header = (
        f"The following one-shot scheduled task{'s were' if plural else ' was'} missed while Hare was not running. "
        f"{'They have' if plural else 'It has'} already been removed from .hare/scheduled_tasks.json.\n\n"
        f"Do NOT execute {'these prompts' if plural else 'this prompt'} yet. "
        f"First use the AskUserQuestion tool to ask whether to run {'each one' if plural else 'it'} now. "
        f"Only execute if the user confirms."
    )
    blocks: list[str] = []
    for t in missed:
        meta = (
            f"[{cron_to_human(t.cron)}, "
            f"created {__import__('datetime').datetime.fromtimestamp(t.created_at / 1000).isoformat()}]"
        )
        # Count backtick runs to pick a safe fence length
        import re as _re

        runs = _re.findall(r"`+", t.prompt)
        longest = max((len(x) for x in runs), default=0)
        fence = "`" * max(3, longest + 1)
        blocks.append(f"{meta}\n{fence}\n{t.prompt}\n{fence}")
    return f"{header}\n\n" + "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# CronScheduler
# ---------------------------------------------------------------------------


@dataclass
class CronScheduler:
    """Active cron-job scheduler with an async background tick loop.

    Public API mirrors the TypeScript ``CronScheduler`` interface so callers
    that already destructure ``{start, stop, getNextFireTime}`` continue to
    work.

    Attributes
    ----------
    start : Callable[[], None]
        Begin the background scheduling loop.  Must be called from within a
        running asyncio event loop.  Idempotent — calling ``start()`` on an
        already-started scheduler is a no-op.
    stop : Callable[[], None]
        Halt the background scheduling loop and release the lock.  Idempotent.
    get_next_fire_time : Callable[[], float | None]
        Return the earliest upcoming fire time (epoch ms), or ``None`` if no
        task is currently scheduled.
    add_task : Callable[..., Coroutine]
        Add a task to the running scheduler (async).  Use ``await``.
    remove_task : Callable[..., Coroutine]
        Remove a task from the running scheduler (async).  Use ``await``.
    list_tasks : Callable[[], list[CronTask]]
        Return all tasks currently tracked by the scheduler.
    is_running : Callable[[], bool]
        Return ``True`` when the background tick loop is active.
    force_reload : Callable[..., Coroutine]
        Force an immediate reload of durable tasks from disk (async).
    get_status : Callable[[], dict[str, Any]]
        Return a dictionary of scheduler health and state information.
    """

    start: Callable[[], None]
    stop: Callable[[], None]
    get_next_fire_time: Callable[[], float | None]

    # Extended API (added for functional parity with cronScheduler.ts)
    add_task: Callable[..., Any]  # async: (task: CronTask, *, session_only: bool = False) -> str
    remove_task: Callable[..., Any]  # async: (task_id: str) -> bool
    list_tasks: Callable[[], list[CronTask]]
    is_running: Callable[[], bool]
    force_reload: Callable[..., Any]  # async: () -> None
    get_status: Callable[[], dict[str, Any]]


# ---------------------------------------------------------------------------
# Internal scheduler state
# ---------------------------------------------------------------------------


@dataclass
class _TaskFireState:
    """Per-task tracking inside the scheduler loop."""

    task: CronTask
    next_fire_ms: float | None = None
    last_fired_at_ms: float = 0.0
    is_session_only: bool = False
    # Track how many times on_fire has been skipped due to error
    consecutive_failures: int = 0
    # When this task was first loaded into the scheduler (epoch ms)
    loaded_at_ms: float = 0.0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_cron_scheduler(options: dict[str, Any]) -> CronScheduler:
    """Create a live ``CronScheduler`` with all dependencies wired in.

    Parameters
    ----------
    options : dict
        Recognized keys:

        - ``on_fire`` (``Callable[[CronTask], None]``, **required**):
          Invoked synchronously for each due task.
        - ``dir`` (``str | None``): Project directory for durable tasks.
          Defaults to the project root from ``bootstrap.state``.
        - ``lock_identity`` (``str | None``): Override for the lock-file
          session identity.  Defaults to the current session id.
        - ``tick_interval_ms`` (``int``): Override tick interval.
          Default: ``DEFAULT_TICK_INTERVAL_MS`` (1 000 ms).
        - ``reload_interval_ms`` (``int``): Override durable-task reload
          interval.  Default: ``DEFAULT_RELOAD_INTERVAL_MS`` (5 000 ms).
        - ``recurring_max_age_ms`` (``int``): Override max age for recurring
          non-permanent tasks.  Default: ``DEFAULT_RECURRING_MAX_AGE_MS``.
        - ``get_jitter_config`` (``Callable[[], CronJitterConfig]``): Override
          jitter-config supplier.  Default: ``cron_jitter_config.get_cron_jitter_config``.

    Returns
    -------
    CronScheduler
        A scheduler whose ``start()`` / ``stop()`` methods control the
        background tick loop.
    """
    on_fire: Callable[[CronTask], None] = options.get("on_fire")  # type: ignore[assignment]
    if on_fire is None:
        raise ValueError("CronScheduler requires an 'on_fire' callback")

    _dir: str | None = options.get("dir")
    _lock_identity: str | None = options.get("lock_identity")
    _tick_ms: int = options.get("tick_interval_ms", DEFAULT_TICK_INTERVAL_MS)
    _reload_ms: int = options.get("reload_interval_ms", DEFAULT_RELOAD_INTERVAL_MS)
    _recurring_max_age_ms: int = options.get(
        "recurring_max_age_ms", DEFAULT_RECURRING_MAX_AGE_MS
    )
    _get_jitter: Callable[[], CronJitterConfig] = options.get(
        "get_jitter_config", get_cron_jitter_config
    )

    # ---- Internal state ------------------------------------------------

    _task_states: dict[str, _TaskFireState] = {}
    _state_lock: asyncio.Lock = asyncio.Lock()
    _bg_task: asyncio.Task[None] | None = None
    _running: bool = False
    _shutting_down: bool = False
    _has_lock: bool = False
    _last_reload_ms: float = 0.0
    _shutdown_event: asyncio.Event | None = None

    # ---- Lock options helper -------------------------------------------

    def _lock_opts() -> dict[str, Any]:
        """Build the options dict for lock acquire/release calls."""
        opts: dict[str, Any] = {}
        if _dir is not None:
            opts["dir"] = _dir
        if _lock_identity is not None:
            opts["lock_identity"] = _lock_identity
        return opts

    # ---- Task loading --------------------------------------------------

    async def _reload_durable_tasks(now_ms: float) -> None:
        """Reload durable tasks from disk if the reload interval has elapsed."""
        nonlocal _last_reload_ms
        if now_ms - _last_reload_ms < _reload_ms:
            return

        try:
            file_tasks = await read_cron_tasks(_dir)
        except Exception:
            log_for_debugging("[CronScheduler] Failed to read durable tasks; will retry")
            return

        _last_reload_ms = now_ms

        # Track which durable task IDs we *already* know about so we don't
        # clobber in-memory last-fired state.
        existing_ids = {
            tid for tid, st in _task_states.items() if not st.is_session_only
        }
        incoming_ids: set[str] = set()

        async with _state_lock:
            for t in file_tasks:
                incoming_ids.add(t.id)
                if t.id in _task_states:
                    existing_state = _task_states[t.id]
                    # Preserve last-fired tracking but update the task object
                    # (e.g. cron expression may have changed).  Take the max
                    # last_fired_at to avoid rewinding after a disk write by
                    # another process.
                    if (
                        t.last_fired_at is not None
                        and existing_state.last_fired_at_ms > 0
                        and t.last_fired_at > existing_state.last_fired_at_ms
                    ):
                        existing_state.last_fired_at_ms = t.last_fired_at
                    existing_state.task = t
                else:
                    st = _TaskFireState(
                        task=t,
                        is_session_only=False,
                        loaded_at_ms=now_ms,
                    )
                    _task_states[t.id] = st

            # Remove state for tasks that no longer exist on disk.
            removed = existing_ids - incoming_ids
            for tid in removed:
                _task_states.pop(tid, None)

    def _load_session_tasks() -> None:
        """Pull session-only tasks from in-memory bootstrap state."""
        try:
            from hare.bootstrap.state import get_session_cron_tasks  # type: ignore[import-not-found]
        except ImportError:
            return

        try:
            raw_list = get_session_cron_tasks()
        except Exception:
            log_for_debugging("[CronScheduler] Failed to read session cron tasks")
            return

        session_ids: set[str] = set()
        now_ms = time.time() * 1000.0

        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            tid = raw.get("id")
            if not tid or not isinstance(tid, str):
                continue
            session_ids.add(tid)

            # Validate cron expression — skip malformed tasks.
            cron_raw = str(raw.get("cron", "* * * * *"))
            if parse_cron_expression(cron_raw) is None:
                log_for_debugging(
                    f"[CronScheduler] Skipping session task {tid} with invalid cron '{cron_raw}'"
                )
                continue

            # Build a CronTask from the raw dict.
            # The dict format mirrors _task_to_json in cron_tasks.py.
            task = CronTask(
                id=tid,
                cron=cron_raw,
                prompt=str(raw.get("prompt", "")),
                created_at=float(raw.get("createdAt", raw.get("created_at", 0))),
                last_fired_at=(
                    float(raw["lastFiredAt"])
                    if raw.get("lastFiredAt") is not None
                    else None
                ),
                recurring=bool(raw.get("recurring")) if raw.get("recurring") else None,
                permanent=bool(raw.get("permanent")) if raw.get("permanent") else None,
                durable=bool(raw.get("durable")) if raw.get("durable") else None,
                agent_id=str(raw["agentId"]) if raw.get("agentId") else None,
            )

            if tid in _task_states:
                _task_states[tid].task = task
                _task_states[tid].is_session_only = True
            else:
                _task_states[tid] = _TaskFireState(
                    task=task,
                    is_session_only=True,
                    loaded_at_ms=now_ms,
                )

        # Prune stale session-only state.
        stale = [
            tid
            for tid, st in _task_states.items()
            if st.is_session_only and tid not in session_ids
        ]
        for tid in stale:
            _task_states.pop(tid, None)

    # ---- Fire-time computation -----------------------------------------

    def _compute_next_fire(
        state: _TaskFireState, now_ms: float, cfg: CronJitterConfig
    ) -> float | None:
        """Compute the next jittered fire time for *state.task*.

        Respects one-shot vs recurring jitter rules and enforces the
        recurring max-age guard.

        Returns ``None`` when the task should not fire (e.g. aged-out,
        invalid cron expression, or no upcoming match within the scan window).
        """
        task = state.task

        # Aged-out recurring tasks should not fire.
        if is_recurring_task_aged(task, now_ms, _recurring_max_age_ms):
            return None

        # Validate cron expression — if somehow we have a malformed one, skip.
        if parse_cron_expression(task.cron) is None:
            log_for_debugging(
                f"[CronScheduler] Task {task.id} has invalid cron expression "
                f"'{task.cron}'; will not compute next fire time"
            )
            return None

        base_from_ms = max(now_ms, state.last_fired_at_ms + 1)

        if task.recurring:
            raw_next = next_cron_run_ms(task.cron, base_from_ms)
            if raw_next is None:
                return None
            # Compute the interval width for jitter: t2 - t1.
            t1 = raw_next
            t2 = next_cron_run_ms(task.cron, t1 + 1)
            if t2 is None:
                # Next-run returned None for t1+1 — use t1 directly.
                return t1
            interval_ms = t2 - t1
            jitter_ms = compute_recurring_jitter_ms(
                float(interval_ms), task.id, cfg
            )
            result = t1 + jitter_ms
            # Clamp: fire time should never be in the past.
            return max(result, now_ms)

        # One-shot task: use the one-shot jittered fire time.
        raw_next = next_cron_run_ms(task.cron, base_from_ms)
        if raw_next is None:
            return None
        jittered = compute_one_shot_jittered_fire_time_ms(raw_next, task.id, cfg)
        return max(jittered, now_ms)

    # ---- Task removal --------------------------------------------------

    async def _remove_durable_task(task_id: str) -> None:
        """Remove a one-shot durable task from disk."""
        try:
            await remove_cron_tasks([task_id], _dir)
        except Exception:
            log_for_debugging(
                f"[CronScheduler] Failed to remove durable task {task_id}"
            )

    async def _remove_session_task(task_id: str) -> None:
        """Remove a one-shot session-only task from in-memory state."""
        try:
            from hare.bootstrap.state import (  # type: ignore[import-not-found]
                remove_session_cron_tasks,
            )
        except ImportError:
            return
        try:
            remove_session_cron_tasks([task_id])
        except Exception:
            log_for_debugging(
                f"[CronScheduler] Failed to remove session task {task_id}"
            )

    # ---- Fire loop -----------------------------------------------------

    async def _fire_due_tasks(now_ms: float) -> None:
        """Iterate all known tasks and fire those whose next fire time has
        arrived.

        Skips tasks that are in a failure backoff state (>=
        ``MAX_CONSECUTIVE_FAILURES`` consecutive failures).  Only updates
        ``last_fired_at_ms`` and removes one-shot tasks on successful fire —
        a failed callback does not consume the task so it can be retried
        (up to the limit).
        """
        # Bail out early if we are shutting down — do not fire new tasks
        # during a graceful stop.
        if _shutting_down:
            return

        cfg = _get_jitter()

        async with _state_lock:
            # Refresh next-fire times.
            for state in _task_states.values():
                # Reset next-fire for tasks that were just loaded (loaded_at_ms
                # is close to now_ms) to ensure immediate computation.
                state.next_fire_ms = _compute_next_fire(state, now_ms, cfg)

            # Collect due tasks (sort by next_fire for deterministic ordering).
            due = sorted(
                (
                    st
                    for st in _task_states.values()
                    if st.next_fire_ms is not None
                    and st.next_fire_ms <= now_ms
                    and st.consecutive_failures < MAX_CONSECUTIVE_FAILURES
                ),
                key=lambda st: st.next_fire_ms or float("inf"),
            )

        fired_ids: list[str] = []
        failed_ids: list[str] = []

        for state in due:
            # Re-check shutdown flag inside the loop so long batches can
            # be interrupted early.
            if _shutting_down:
                break

            task = state.task
            success = False

            try:
                on_fire(task)
                success = True
            except Exception as exc:
                log_for_debugging(
                    f"[CronScheduler] on_fire callback raised for task {task.id}: "
                    f"{type(exc).__name__}: {exc}"
                )

            async with _state_lock:
                # Re-fetch state — it may have been removed by another operation.
                current = _task_states.get(task.id)
                if current is None:
                    continue

                if success:
                    current.last_fired_at_ms = now_ms
                    current.consecutive_failures = 0
                    fired_ids.append(task.id)

                    # One-shot tasks are removed after firing.
                    if not task.recurring:
                        if current.is_session_only:
                            await _remove_session_task(task.id)
                        else:
                            await _remove_durable_task(task.id)
                        _task_states.pop(task.id, None)
                else:
                    # Track consecutive failures.  After MAX_CONSECUTIVE_FAILURES
                    # the task will be skipped in future ticks.
                    current.consecutive_failures += 1
                    failed_ids.append(task.id)
                    log_for_debugging(
                        f"[CronScheduler] Task {task.id} failed {current.consecutive_failures} "
                        f"time(s) consecutively (max {MAX_CONSECUTIVE_FAILURES})"
                    )

        # Persist last_fired_at for recurring tasks that fired successfully.
        if fired_ids:
            recurring_fired = [
                tid
                for tid in fired_ids
                if tid in _task_states and _task_states[tid].task.recurring
            ]
            if recurring_fired:
                try:
                    await __import__(
                        "hare.utils.cron_tasks", fromlist=["mark_cron_tasks_fired"]
                    ).mark_cron_tasks_fired(recurring_fired, now_ms, _dir)
                except Exception:
                    log_for_debugging(
                        "[CronScheduler] Failed to persist lastFiredAt for recurring tasks"
                    )

    # ---- Aged-task cleanup ---------------------------------------------

    async def _cleanup_aged_tasks(now_ms: float) -> None:
        """Remove aged-out recurring non-permanent tasks."""
        aged_ids: list[str] = []

        async with _state_lock:
            for tid, state in list(_task_states.items()):
                if is_recurring_task_aged(state.task, now_ms, _recurring_max_age_ms):
                    aged_ids.append(tid)

        if not aged_ids:
            return

        durable_aged = [
            tid for tid in aged_ids if not _task_states[tid].is_session_only
        ]
        session_aged = [
            tid for tid in aged_ids if _task_states[tid].is_session_only
        ]

        if durable_aged:
            try:
                await remove_cron_tasks(durable_aged, _dir)
            except Exception:
                log_for_debugging("[CronScheduler] Failed to remove aged durable tasks")

        if session_aged:
            try:
                from hare.bootstrap.state import (  # type: ignore[import-not-found]
                    remove_session_cron_tasks,
                )

                remove_session_cron_tasks(session_aged)
            except Exception:
                log_for_debugging("[CronScheduler] Failed to remove aged session tasks")

        async with _state_lock:
            for tid in aged_ids:
                _task_states.pop(tid, None)

        if aged_ids:
            log_for_debugging(
                f"[CronScheduler] Cleaned up {len(aged_ids)} aged recurring task(s)"
            )

    # ---- Missed-task detection -----------------------------------------

    async def _detect_missed_tasks(now_ms: float) -> list[CronTask]:
        """Find one-shot durable tasks whose fire time has passed.

        These are tasks that were scheduled while Hare was not running
        and whose cron fire time has already elapsed.  The caller should
        present these to the user rather than executing them automatically.

        Only considers one-shot tasks (not recurring), since recurring tasks
        that "missed" a fire window should just wait for their next window.
        """
        try:
            file_tasks = await read_cron_tasks(_dir)
        except Exception:
            return []

        return find_missed_tasks(file_tasks, now_ms)

    # ---- Tick loop -----------------------------------------------------

    async def _tick_loop() -> None:
        """Main scheduling loop.  Runs until ``_running`` is set to False."""
        nonlocal _shutdown_event
        _shutdown_event = asyncio.Event()

        log_for_debugging("[CronScheduler] Tick loop started")

        # Detect one-shot tasks missed while Hare was not running.
        # These are reported once at startup so the user can decide whether
        # to execute them.
        try:
            now_ms = time.time() * 1000.0
            missed = await _detect_missed_tasks(now_ms)
            if missed:
                notification = build_missed_task_notification(missed)
                log_for_debugging(
                    f"[CronScheduler] Detected {len(missed)} missed task(s):\n{notification}"
                )
        except Exception:
            log_for_debugging(
                "[CronScheduler] Failed to detect missed tasks on startup"
            )

        while _running and not _shutting_down:
            try:
                now_ms = time.time() * 1000.0

                await _reload_durable_tasks(now_ms)
                _load_session_tasks()
                await _cleanup_aged_tasks(now_ms)
                await _fire_due_tasks(now_ms)

            except Exception:
                log_for_debugging("[CronScheduler] Tick iteration failed; will retry")

            # Wait for the next tick or until shutdown is signaled.
            try:
                await asyncio.wait_for(
                    _shutdown_event.wait(),
                    timeout=_tick_ms / 1000.0,
                )
                # Shutdown was signaled.
                break
            except asyncio.TimeoutError:
                # Normal tick elapsed — loop again.
                pass

        log_for_debugging("[CronScheduler] Tick loop stopped")

        # Give in-flight fire callbacks a brief grace period to complete,
        # then release the lock.
        try:
            await asyncio.sleep(0.1)  # 100 ms for last callback to finish
        except asyncio.CancelledError:
            pass

    # ---- Public API — start / stop ------------------------------------

    def _start() -> None:
        """Begin the background scheduling loop.

        Acquires the scheduler lock.  If the lock cannot be acquired
        (another process is already scheduling), the scheduler silently
        stays idle — only one process per project schedules at a time.

        Must be called from within a running asyncio event loop.

        Idempotent — calling ``start()`` on an already-started scheduler
        is a no-op.
        """
        nonlocal _bg_task, _running, _shutting_down

        if _running:
            return

        # If a previous bg_task is still winding down, wait for it.
        if _bg_task is not None and not _bg_task.done():
            log_for_debugging(
                "[CronScheduler] Background task still active; start() is a no-op "
                "(call stop() and wait before re-starting)"
            )
            return

        _shutting_down = False

        async def _acquire_and_run() -> None:
            """Acquire the scheduler lock, then run the tick loop.  Clean up
            the lock on exit (including cancellation)."""
            nonlocal _has_lock, _running, _shutting_down

            # Acquire lock.
            try:
                _has_lock = await try_acquire_scheduler_lock(_lock_opts())
            except Exception as exc:
                log_for_debugging(
                    f"[CronScheduler] Lock acquisition raised: {type(exc).__name__}: {exc}"
                )
                return

            if not _has_lock:
                log_for_debugging(
                    "[CronScheduler] Could not acquire scheduler lock; "
                    "another process may be scheduling.  Scheduler idle."
                )
                return

            _running = True
            try:
                await _tick_loop()
            except asyncio.CancelledError:
                log_for_debugging("[CronScheduler] Background task cancelled")
            except Exception as exc:
                log_for_debugging(
                    f"[CronScheduler] Background task raised: {type(exc).__name__}: {exc}"
                )
            finally:
                _running = False
                _shutting_down = False
                if _has_lock:
                    try:
                        await release_scheduler_lock(_lock_opts())
                    except Exception:
                        log_for_debugging(
                            "[CronScheduler] Failed to release lock during cleanup"
                        )
                    _has_lock = False
                log_for_debugging("[CronScheduler] Background task finished")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log_for_debugging(
                "[CronScheduler] No running event loop; start() must be called "
                "from within an async context.  Scheduler will NOT start."
            )
            return

        _bg_task = loop.create_task(_acquire_and_run())
        log_for_debugging("[CronScheduler] Background task created")

    def _stop() -> None:
        """Halt the background scheduling loop and release the lock.

        Idempotent — safe to call on an already-stopped scheduler.
        Sets a shutdown flag and signals the tick loop to wake up and exit.
        The lock is released via the background task's ``finally`` block.
        """
        nonlocal _running, _shutting_down

        if not _running and not _shutting_down and (_bg_task is None or _bg_task.done()):
            # Already fully stopped.
            return

        _running = False
        _shutting_down = True

        # Signal the tick loop to wake up and exit.
        if _shutdown_event is not None:
            _shutdown_event.set()

        # Cancel the background task if it is still alive.
        if _bg_task is not None and not _bg_task.done():
            _bg_task.cancel()
            # Note: we do NOT await here because _stop() is synchronous.
            # The cancellation will propagate to the _acquire_and_run task,
            # which will release the lock in its finally block.

        log_for_debugging("[CronScheduler] Stop requested")

    # ---- Public API — task management ----------------------------------

    async def _add_task(
        task: CronTask,
        *,
        session_only: bool = False,
    ) -> str:
        """Add a task to the running scheduler.

        For session-only tasks the task is stored in-memory and will not
        survive a restart.  For durable tasks the task is persisted to
        ``.hare/scheduled_tasks.json``.

        Parameters
        ----------
        task : CronTask
            The task to add.  Must have a valid cron expression.
        session_only : bool
            If True, store only in memory (session scope).  Default False.

        Returns
        -------
        str
            The task ID.

        Raises
        ------
        ValueError
            If the cron expression is invalid.
        """
        # Validate cron expression.
        errors: list[str] = []
        try:
            from hare.utils.cron import validate_cron_expression

            errors = validate_cron_expression(task.cron)
        except ImportError:
            if parse_cron_expression(task.cron) is None:
                errors = [f"Invalid cron expression: {task.cron!r}"]

        if errors:
            raise ValueError(
                f"Cannot add task {task.id}: invalid cron expression "
                f"{task.cron!r} — {'; '.join(errors)}"
            )

        now_ms = time.time() * 1000.0

        if session_only:
            # Store in-memory via bootstrap state.
            task_dict: dict[str, Any] = {
                "id": task.id,
                "cron": task.cron,
                "prompt": task.prompt,
                "createdAt": task.created_at,
            }
            if task.last_fired_at is not None:
                task_dict["lastFiredAt"] = task.last_fired_at
            if task.recurring:
                task_dict["recurring"] = True
            if task.permanent:
                task_dict["permanent"] = True
            if task.durable:
                task_dict["durable"] = True
            if task.agent_id:
                task_dict["agentId"] = task.agent_id

            try:
                from hare.bootstrap.state import (  # type: ignore[import-not-found]
                    add_session_cron_task,
                )

                add_session_cron_task(task_dict)
            except ImportError:
                log_for_debugging(
                    "[CronScheduler] Cannot add session task: bootstrap.state unavailable"
                )

            async with _state_lock:
                st = _TaskFireState(
                    task=task,
                    is_session_only=True,
                    loaded_at_ms=now_ms,
                )
                _task_states[task.id] = st

            log_for_debugging(
                f"[CronScheduler] Added session-only task {task.id} "
                f"('{cron_to_human(task.cron)}')"
            )
        else:
            # Persist to durable storage.
            try:
                existing = await read_cron_tasks(_dir)
                existing.append(task)
                await write_cron_tasks(existing, _dir)
            except Exception as exc:
                log_for_debugging(
                    f"[CronScheduler] Failed to persist durable task {task.id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                raise

            async with _state_lock:
                st = _TaskFireState(
                    task=task,
                    is_session_only=False,
                    loaded_at_ms=now_ms,
                )
                _task_states[task.id] = st

            log_for_debugging(
                f"[CronScheduler] Added durable task {task.id} "
                f"('{cron_to_human(task.cron)}')"
            )

        # Compute next fire time immediately so the task is tracked.
        cfg = _get_jitter()
        async with _state_lock:
            if task.id in _task_states:
                _task_states[task.id].next_fire_ms = _compute_next_fire(
                    _task_states[task.id], now_ms, cfg
                )

        return task.id

    async def _remove_task(task_id: str) -> bool:
        """Remove a task from the running scheduler.

        For durable tasks the removal is persisted to disk.  For session-only
        tasks the task is removed from in-memory state only.

        Parameters
        ----------
        task_id : str
            The ID of the task to remove.

        Returns
        -------
        bool
            ``True`` if a task was removed, ``False`` if no task with that
            ID was found.
        """
        async with _state_lock:
            state = _task_states.get(task_id)
            if state is None:
                return False

            is_session = state.is_session_only

        if is_session:
            await _remove_session_task(task_id)
        else:
            await _remove_durable_task(task_id)

        async with _state_lock:
            _task_states.pop(task_id, None)

        log_for_debugging(f"[CronScheduler] Removed task {task_id}")
        return True

    # ---- Public API — query --------------------------------------------

    def _list_tasks() -> list[CronTask]:
        """Return all tasks currently tracked by the scheduler.

        The returned list is a snapshot; tasks may have been added or removed
        since the call.
        """
        return [st.task for st in _task_states.values()]

    def _is_running() -> bool:
        """Return ``True`` when the background tick loop is active."""
        return _running

    async def _force_reload() -> None:
        """Force an immediate reload of durable tasks from disk.

        Resets the reload interval timer so the next call to
        ``_reload_durable_tasks`` will actually read from disk.  Also
        immediately reloads session tasks.
        """
        nonlocal _last_reload_ms
        _last_reload_ms = 0.0
        now_ms = time.time() * 1000.0
        await _reload_durable_tasks(now_ms)
        _load_session_tasks()
        log_for_debugging("[CronScheduler] Forced reload of durable and session tasks")

    def _get_status() -> dict[str, Any]:
        """Return a dictionary of scheduler health and state information.

        Useful for debugging and monitoring (e.g. "how many tasks are
        currently tracked?").
        """
        now_ms = time.time() * 1000.0
        next_fire = _get_next_fire_time()
        return {
            "running": _running,
            "shutting_down": _shutting_down,
            "has_lock": _has_lock,
            "task_count": len(_task_states),
            "durable_task_count": sum(
                1 for st in _task_states.values() if not st.is_session_only
            ),
            "session_task_count": sum(
                1 for st in _task_states.values() if st.is_session_only
            ),
            "next_fire_time_ms": next_fire,
            "next_fire_in_s": (
                (next_fire - now_ms) / 1000.0 if next_fire is not None else None
            ),
            "last_reload_ms": _last_reload_ms,
            "tick_interval_ms": _tick_ms,
            "reload_interval_ms": _reload_ms,
            "recurring_max_age_ms": _recurring_max_age_ms,
            "failed_task_ids": [
                tid
                for tid, st in _task_states.items()
                if st.consecutive_failures > 0
            ],
            "bg_task_active": _bg_task is not None and not _bg_task.done(),
        }

    def _get_next_fire_time() -> float | None:
        """Return the earliest upcoming fire time (epoch ms), or ``None``.

        Useful for UI indicators ("next scheduled task in 3 minutes").
        """
        if not _task_states:
            return None
        now_ms = time.time() * 1000.0
        cfg = _get_jitter()
        earliest: float | None = None
        for state in _task_states.values():
            # Skip tasks that are in failure backoff.
            if state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                continue
            nf = _compute_next_fire(state, now_ms, cfg)
            if nf is not None and (earliest is None or nf < earliest):
                earliest = nf
        return earliest

    # ---- Assemble ------------------------------------------------------

    return CronScheduler(
        start=_start,
        stop=_stop,
        get_next_fire_time=_get_next_fire_time,
        add_task=_add_task,
        remove_task=_remove_task,
        list_tasks=_list_tasks,
        is_running=_is_running,
        force_reload=_force_reload,
        get_status=_get_status,
    )


# ---------------------------------------------------------------------------
# Convenience: start a scheduler with minimal boilerplate
# ---------------------------------------------------------------------------


def start_cron_scheduler(
    on_fire: Callable[[CronTask], None],
    *,
    dir_path: str | None = None,
    lock_identity: str | None = None,
    tick_interval_ms: int = DEFAULT_TICK_INTERVAL_MS,
    reload_interval_ms: int = DEFAULT_RELOAD_INTERVAL_MS,
    recurring_max_age_ms: int = DEFAULT_RECURRING_MAX_AGE_MS,
) -> CronScheduler:
    """Create and immediately start a ``CronScheduler``.

    Convenience wrapper around ``create_cron_scheduler`` + ``scheduler.start()``.
    The returned scheduler can be stopped later via ``scheduler.stop()``.

    Parameters
    ----------
    on_fire : Callable[[CronTask], None]
        Callback invoked when a scheduled task is due.
    dir_path : str | None
        Project directory for durable task storage.
    lock_identity : str | None
        Override for the lock-file session identity.
    tick_interval_ms : int
        Scheduler tick interval in milliseconds.
    reload_interval_ms : int
        Durable-task file reload interval in milliseconds.
    recurring_max_age_ms : int
        Max age for recurring non-permanent tasks in milliseconds.

    Returns
    -------
    CronScheduler
        A running scheduler.  Call ``.stop()`` to shut it down.
    """
    opts: dict[str, Any] = {
        "on_fire": on_fire,
        "tick_interval_ms": tick_interval_ms,
        "reload_interval_ms": reload_interval_ms,
        "recurring_max_age_ms": recurring_max_age_ms,
    }
    if dir_path is not None:
        opts["dir"] = dir_path
    if lock_identity is not None:
        opts["lock_identity"] = lock_identity

    scheduler = create_cron_scheduler(opts)
    scheduler.start()
    return scheduler


# ---------------------------------------------------------------------------
# Missed-task reporting (one-shot helper for REPL startup)
# ---------------------------------------------------------------------------


async def detect_and_report_missed_tasks(
    dir_path: str | None = None,
) -> list[CronTask]:
    """Detect one-shot tasks whose fire time passed while not running.

    Returns the list of missed ``CronTask`` objects so the caller can
    decide how to present them to the user.  This is a standalone helper
    that does not require a running scheduler.

    Parameters
    ----------
    dir_path : str | None
        Project directory for durable task storage.

    Returns
    -------
    list[CronTask]
        Tasks whose next fire time is in the past relative to now.
    """
    try:
        tasks = await read_cron_tasks(dir_path)
    except Exception:
        return []

    now_ms = time.time() * 1000.0
    return find_missed_tasks(tasks, now_ms)


# ---------------------------------------------------------------------------
# Module-level scheduler lifecycle helpers
# ---------------------------------------------------------------------------

# Singleton reference for the module-level scheduler (set by the REPL
# bootstrap when it creates the scheduler for the session).
_module_scheduler: CronScheduler | None = None


def set_module_scheduler(scheduler: CronScheduler | None) -> None:
    """Register (or clear) the module-level scheduler singleton.

    Called by the REPL bootstrap at session start/stop to make the scheduler
    available to tools like ``CronCreate`` / ``CronDelete`` that need to
    inspect or mutate the running scheduler without plumbing a reference
    through every call.
    """
    global _module_scheduler
    _module_scheduler = scheduler


def get_module_scheduler() -> CronScheduler | None:
    """Return the module-level scheduler singleton, or ``None``."""
    return _module_scheduler


async def stop_module_scheduler() -> None:
    """Stop and clear the module-level scheduler singleton (if any).

    Safe to call even when no scheduler is registered — the function is a
    no-op in that case.
    """
    global _module_scheduler
    if _module_scheduler is not None:
        _module_scheduler.stop()
        # Give the background task a moment to release the lock.
        await asyncio.sleep(0.1)
        _module_scheduler = None
