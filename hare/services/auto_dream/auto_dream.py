"""
Auto-dream: background memory consolidation subagent.

Port of: src/services/autoDream/autoDream.ts (325 lines)

Background memory consolidation. Fires the /dream prompt as a forked
subagent when time-gate passes AND enough sessions have accumulated.

Gate order (cheapest first):
  1. Enabled: Kairos/remote/auto-memory/config
  2. Time: hours since lastConsolidatedAt >= minHours (one stat)
  3. Scan throttle: SESSION_SCAN_INTERVAL_MS
  4. Sessions: transcript count with mtime > lastConsolidatedAt >= minSessions
  5. Lock: no other process mid-consolidation
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable

from hare.utils.debug import log_for_debugging

# ---------------------------------------------------------------------------
# Constants matching TS
# ---------------------------------------------------------------------------

SESSION_SCAN_INTERVAL_MS = 10 * 60 * 1000  # 10 minutes

_DEFAULTS: dict[str, int] = {
    "min_hours": 24,
    "min_sessions": 5,
}

# Retry backoff: base delay and cap after consecutive failures.
RETRY_BASE_DELAY_SECONDS = 900  # 15 minutes
RETRY_MAX_BACKOFF_SECONDS = 8 * 3600  # 8 hours
RETRY_JITTER_PCT = 0.15  # +/- 15% jitter to avoid thundering herd

# Consolidation history log file inside memory dir.
HISTORY_LOG_FILENAME = ".consolidation-history.jsonl"

# ---------------------------------------------------------------------------
# Module-level closure (TS: `let runner: ... | null = null`)
# ---------------------------------------------------------------------------

_runner: Callable[..., Any] | None = None

# Guard against concurrent auto-dream fires within the same process.
# The cross-process lock (consolidation_lock.py) protects across processes,
# but within a single process two turns could both pass the gate before
# either acquires the lock. This flag closes that window.
_is_running: bool = False

# Tracks whether init_auto_dream() has been called. execute_auto_dream()
# is a no-op until initialized. Separate from _runner is None so that
# _reset_auto_dream() can distinguish "never initialized" from "reset".
_initialized: bool = False

# Retry state: tracks consecutive failure count and last failure time for
# backoff computation. Reset on success.
_retry_state: dict[str, float | int] = {
    "consecutive_failures": 0,
    "last_failure_epoch_seconds": 0.0,
    "next_retry_after_epoch_seconds": 0.0,
}


# ---------------------------------------------------------------------------
# Config (TS getConfig L73-93)
# ---------------------------------------------------------------------------


def _get_config() -> dict[str, int]:
    """Thresholds from tengu_onyx_plover. Returns scheduling knobs only.
    Defensive per-field validation since GB cache can return stale values.
    """
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        raw = get_feature_value_cached_may_be_stale("tengu_onyx_plover", None)
    except Exception:
        raw = None

    if not isinstance(raw, dict):
        raw = {}

    def _valid_int(val: Any, default: int) -> int:
        if isinstance(val, (int, float)) and val > 0 and val == val:  # NaN check
            return int(val)
        return default

    return {
        "min_hours": _valid_int(raw.get("minHours"), _DEFAULTS["min_hours"]),
        "min_sessions": _valid_int(raw.get("minSessions"), _DEFAULTS["min_sessions"]),
    }


# ---------------------------------------------------------------------------
# Gate checks (TS isGateOpen L95-100, isForced L105-107)
# ---------------------------------------------------------------------------


def _is_gate_open() -> bool:
    """Check if auto-dream should be considered at all."""
    try:
        from hare.bootstrap.state import get_kairos_active, get_is_remote_mode
    except ImportError:
        return False

    if get_kairos_active():
        return False  # KAIROS mode uses disk-skill dream
    if get_is_remote_mode():
        return False

    try:
        from hare.memdir.paths import is_auto_memory_enabled
    except ImportError:
        return False

    if not is_auto_memory_enabled():
        return False

    try:
        from hare.services.auto_dream.config import is_auto_dream_enabled

        return is_auto_dream_enabled()
    except ImportError:
        return False


def _is_forced() -> bool:
    """Test override. Bypasses enabled/time/session gates but NOT the lock
    or memory-dir precondition.

    Enabled via HARE_AUTO_DREAM_FORCE=1 env var. Also accepts
    CLAUDE_CODE_AUTO_DREAM_FORCE for consistency with other env vars.
    Always false in production.
    """
    return (
        os.environ.get("HARE_AUTO_DREAM_FORCE", "").lower() in ("1", "true", "yes")
        or os.environ.get("CLAUDE_CODE_AUTO_DREAM_FORCE", "").lower()
        in ("1", "true", "yes")
    )


# ---------------------------------------------------------------------------
# Idle gate helpers (TS isGateOpen has no idle check; this is an improvement)
# ---------------------------------------------------------------------------


def _last_user_message_epoch_seconds(
    messages: list[dict[str, Any]],
) -> float:
    """Scan messages in reverse for the most recent user message timestamp.

    Returns epoch seconds (float). Returns 0.0 if no user message is found
    (no known last activity — idle gate treats this as "idle").
    """
    for msg in reversed(messages):
        msg_type = msg.get("type") if isinstance(msg, dict) else getattr(msg, "type", None)
        if msg_type != "user":
            continue

        # Prefer timestamp field, fall back to created_at
        ts = msg.get("timestamp") if isinstance(msg, dict) else getattr(msg, "timestamp", None)
        if ts is None:
            ts = msg.get("created_at") if isinstance(msg, dict) else getattr(msg, "created_at", None)

        if ts is not None:
            try:
                val = float(ts)
                # If value looks like milliseconds (> 1e12), convert to seconds
                if val > 1e12:
                    return val / 1000.0
                return val
            except (TypeError, ValueError):
                continue

    return 0.0


def _is_idle_long_enough(
    last_user_message_at: float, min_idle_minutes: int
) -> bool:
    """Check whether the user has been idle long enough for background work.

    last_user_message_at: epoch seconds of the last user message.
    Returns True if min_idle_minutes is 0 (no idle requirement), or if the
    user has been idle at least that long.
    """
    if min_idle_minutes <= 0:
        return True
    if last_user_message_at <= 0:
        # No known last activity — assume idle (conservative: fire)
        return True
    idle_seconds = time.time() - last_user_message_at
    return idle_seconds >= min_idle_minutes * 60


# ---------------------------------------------------------------------------
# Memory directory health check
# ---------------------------------------------------------------------------


def _validate_memory_directory(memory_root: str) -> dict[str, Any]:
    """Validate that the memory directory is usable for consolidation.

    Checks: path exists and is a directory, is writable, contains an entrypoint
    file, and has no corruption indicators (e.g. zero-byte directory markers
    from interrupted writes).

    Returns a dict with:
      - valid: bool — False means consolidation should be skipped
      - issues: list[str] — human-readable warnings
      - file_count: int — number of immediate child files (for diagnostics)
      - entrypoint_exists: bool — whether ENTRYPOINT_NAME is present
    """
    import os as _os

    issues: list[str] = []

    try:
        s = _os.stat(memory_root)
    except OSError:
        return {
            "valid": False,
            "issues": ["Memory directory does not exist"],
            "file_count": 0,
            "entrypoint_exists": False,
        }

    if not (_os.path.isdir(memory_root) if hasattr(_os.path, "isdir") else True):
        issues.append("Memory path is not a directory")

    # Check writability by testing we can create a temp file
    writable = False
    try:
        probe = _os.path.join(memory_root, ".consolidation-probe")
        with open(probe, "w") as f:
            f.write("ok")
        _os.unlink(probe)
        writable = True
    except OSError:
        issues.append("Memory directory is not writable")

    # Count files and check for entrypoint
    file_count = 0
    entrypoint_exists = False
    try:
        from hare.memdir.memdir import ENTRYPOINT_NAME

        for entry in _os.scandir(memory_root):
            if entry.is_file():
                file_count += 1
                if entry.name == ENTRYPOINT_NAME:
                    entrypoint_exists = True
    except OSError:
        issues.append("Cannot list memory directory contents")

    # Check for zero-byte files that indicate interrupted writes (corruption
    # indicators — they exist but carry no content)
    zero_byte_files: list[str] = []
    try:
        for entry in _os.scandir(memory_root):
            if entry.is_file() and entry.stat().st_size == 0:
                zero_byte_files.append(entry.name)
    except OSError:
        pass

    if zero_byte_files:
        issues.append(
            f"Zero-byte files detected (possible interrupted writes): "
            f"{', '.join(zero_byte_files[:5])}"
            + (f" and {len(zero_byte_files) - 5} more" if len(zero_byte_files) > 5 else "")
        )

    valid = len(issues) == 0 or all(
        "Zero-byte" in i for i in issues
    )  # zero-byte is a warning, not a blocker

    return {
        "valid": valid,
        "issues": issues,
        "file_count": file_count,
        "entrypoint_exists": entrypoint_exists,
        "writable": writable,
    }


def _check_memory_dir_health(memory_root: str) -> bool:
    """Quick health gate: returns True if the memory directory is ready.

    If the directory doesn't exist, creates it (it may be a first-time run).
    Returns False only for hard failures like permission errors.
    """
    import os as _os

    if not _os.path.exists(memory_root):
        try:
            _os.makedirs(memory_root, exist_ok=True)
            log_for_debugging(f"[autoDream] created memory directory: {memory_root}")
            return True
        except OSError as e:
            log_for_debugging(f"[autoDream] cannot create memory directory: {e}")
            return False

    result = _validate_memory_directory(memory_root)
    if not result["valid"]:
        log_for_debugging(
            f"[autoDream] memory directory unhealthy: {'; '.join(result['issues'])}"
        )
        return False
    return True


# ---------------------------------------------------------------------------
# State reset (for tests — matches _reset_extraction_state in extract_memories)
# ---------------------------------------------------------------------------


def _reset_auto_dream() -> None:
    """Reset all module-level closure state. Called from beforeEach in tests."""
    global _runner, _is_running, _initialized, _retry_state
    _runner = None
    _is_running = False
    _initialized = False
    _retry_state = {
        "consecutive_failures": 0,
        "last_failure_epoch_seconds": 0.0,
        "next_retry_after_epoch_seconds": 0.0,
    }


# ---------------------------------------------------------------------------
# Dream progress watcher (TS makeDreamProgressWatcher L281-313)
# ---------------------------------------------------------------------------


def _make_dream_progress_watcher(
    task_id: str,
    set_app_state: Callable[[Any], None],
) -> Callable[[Any], None]:
    """Watch the forked agent's messages. For each assistant turn, extracts any
    text blocks (the agent's reasoning/summary) and collapses tool_use blocks
    to a count. Edit/Write file_paths are collected for the inline completion
    message.
    """
    FILE_EDIT_TOOL_NAME = "Edit"
    FILE_WRITE_TOOL_NAME = "Write"

    def on_message(msg: Any) -> None:
        # Handle both dict and object messages
        msg_type = msg.get("type") if isinstance(msg, dict) else getattr(msg, "type", None)
        if msg_type != "assistant":
            return

        content = (
            msg.get("message", {}).get("content", [])
            if isinstance(msg, dict)
            else getattr(getattr(msg, "message", None), "content", [])
        )
        if not isinstance(content, list):
            return

        text_parts: list[str] = []
        tool_use_count = 0
        touched_paths: list[str] = []

        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
            else:
                block_type = getattr(block, "type", "")

            if block_type == "text":
                t = (
                    block.get("text", "")
                    if isinstance(block, dict)
                    else getattr(block, "text", "")
                )
                text_parts.append(str(t))
            elif block_type == "tool_use":
                tool_use_count += 1
                name = (
                    block.get("name", "")
                    if isinstance(block, dict)
                    else getattr(block, "name", "")
                )
                if name in (FILE_EDIT_TOOL_NAME, FILE_WRITE_TOOL_NAME):
                    inp = (
                        block.get("input", {})
                        if isinstance(block, dict)
                        else getattr(block, "input", {})
                    )
                    file_path = (
                        inp.get("file_path", "")
                        if isinstance(inp, dict)
                        else getattr(inp, "file_path", "")
                    )
                    if isinstance(file_path, str) and file_path:
                        touched_paths.append(file_path)

        # Register turn with the DreamTask
        try:
            from hare.tasks.dream_task import add_dream_turn, DreamTurn

            turn = DreamTurn(
                text="\n".join(text_parts).strip(),
                tool_use_count=tool_use_count,
            )
            add_dream_turn(task_id, turn, touched_paths, set_app_state)
        except ImportError:
            pass

    return on_message


# ---------------------------------------------------------------------------
# Consolidation history tracking
# ---------------------------------------------------------------------------


def _get_history_log_path(memory_root: str) -> str:
    """Full path to the consolidation history JSONL log file."""
    import os as _os

    return _os.path.join(memory_root, HISTORY_LOG_FILENAME)


def _record_consolidation_attempt(
    memory_root: str,
    *,
    session_count: int,
    hours_since: float,
    success: bool,
    touched_files: list[str] | None = None,
    error: str | None = None,
    cache_read_tokens: int = 0,
    cache_created_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Append a record to the consolidation history JSONL log.

    Each line is a JSON object with timestamp, session_count, success, and
    optional diagnostics. Best-effort — failures are logged to debug but
    never raised (history loss must not break the dream cycle).
    """
    import json as _json
    import os as _os

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    record: dict[str, Any] = {
        "ts": now_iso,
        "epoch_ms": int(time.time() * 1000),
        "sessions_reviewed": session_count,
        "hours_since_last": round(hours_since, 1),
        "success": success,
        "files_touched": len(touched_files or []),
    }
    if touched_files:
        record["files_touched_list"] = touched_files[:20]  # cap to avoid bloat
    if error:
        record["error"] = error[:500]  # truncate long error messages
    if cache_read_tokens:
        record["cache_read_tokens"] = cache_read_tokens
    if cache_created_tokens:
        record["cache_created_tokens"] = cache_created_tokens
    if output_tokens:
        record["output_tokens"] = output_tokens

    path = _get_history_log_path(memory_root)
    try:
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        log_for_debugging(f"[autoDream] failed to write history log: {e}")


def _read_consolidation_history(
    memory_root: str,
    max_entries: int = 50,
) -> list[dict[str, Any]]:
    """Read recent consolidation history entries (most recent first).

    Used for retry backoff computation and diagnostics. Returns empty list
    if the log file doesn't exist or is unreadable.
    """
    import json as _json
    import os as _os

    path = _get_history_log_path(memory_root)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    for line in reversed(lines):  # most recent first
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(_json.loads(line))
        except _json.JSONDecodeError:
            continue
        if len(entries) >= max_entries:
            break
    return entries


# ---------------------------------------------------------------------------
# Retry backoff computation
# ---------------------------------------------------------------------------


def _compute_retry_backoff_seconds() -> float:
    """Compute seconds until the next auto-dream retry is allowed.

    Uses exponential backoff: base * 2^failures, capped at MAX_BACKOFF.
    Adds +/-15% jitter to avoid thundering herd from multiple processes.

    Returns 0.0 if no retry delay is needed (no failures or backoff expired).
    """
    failures = int(_retry_state["consecutive_failures"])
    if failures <= 0:
        return 0.0

    import random as _random

    # Exponential backoff: 15min, 30min, 1h, 2h, 4h, 8h (capped)
    backoff = RETRY_BASE_DELAY_SECONDS * (2 ** (failures - 1))
    if backoff > RETRY_MAX_BACKOFF_SECONDS:
        backoff = RETRY_MAX_BACKOFF_SECONDS

    # Jitter: +/- 15%
    jitter = backoff * RETRY_JITTER_PCT * (2 * _random.random() - 1)
    backoff += jitter

    next_retry_at = float(_retry_state["last_failure_epoch_seconds"]) + backoff
    remaining = next_retry_at - time.time()
    return max(0.0, remaining)


def _update_retry_state(success: bool) -> None:
    """Update the retry state after a dream attempt.

    On success: reset consecutive failures to 0.
    On failure: increment consecutive failures and record the failure time.
    """
    global _retry_state

    if success:
        _retry_state["consecutive_failures"] = 0
        _retry_state["last_failure_epoch_seconds"] = 0.0
        _retry_state["next_retry_after_epoch_seconds"] = 0.0
    else:
        _retry_state["consecutive_failures"] = int(_retry_state["consecutive_failures"]) + 1
        _retry_state["last_failure_epoch_seconds"] = float(time.time())
        backoff = _compute_retry_backoff_seconds()
        _retry_state["next_retry_after_epoch_seconds"] = float(time.time()) + backoff

        log_for_debugging(
            f"[autoDream] retry backoff: {int(_retry_state['consecutive_failures'])} "
            f"consecutive failures, next retry in {backoff:.0f}s"
        )


def _should_skip_retry_backoff(force: bool) -> bool:
    """Check whether retry backoff should block this attempt.

    Returns True if the attempt should be skipped due to backoff.
    Force bypasses backoff (for manual /dream triggers).
    """
    if force:
        return False

    remaining = _compute_retry_backoff_seconds()
    if remaining > 0:
        log_for_debugging(
            f"[autoDream] retry backoff active — {remaining:.0f}s remaining "
            f"after {int(_retry_state['consecutive_failures'])} failures"
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Dream statistics
# ---------------------------------------------------------------------------


def _compute_dream_statistics(memory_root: str) -> dict[str, Any]:
    """Compute statistics from consolidation history.

    Returns a dict with success_rate, total_runs, total_failures,
    average_sessions_reviewed, and last_success_at (ISO timestamp or empty
    string if never succeeded).
    """
    history = _read_consolidation_history(memory_root)
    if not history:
        return {
            "total_runs": 0,
            "total_successes": 0,
            "total_failures": 0,
            "success_rate": 0.0,
            "average_sessions_reviewed": 0.0,
            "last_success_at": "",
        }

    total = len(history)
    successes = [e for e in history if e.get("success")]
    failures = total - len(successes)

    success_rate = (len(successes) / total * 100) if total > 0 else 0.0
    avg_sessions = (
        sum(e.get("sessions_reviewed", 0) for e in history) / total
        if total > 0
        else 0.0
    )
    last_success = successes[0]["ts"] if successes else ""

    return {
        "total_runs": total,
        "total_successes": len(successes),
        "total_failures": failures,
        "success_rate": round(success_rate, 1),
        "average_sessions_reviewed": round(avg_sessions, 1),
        "last_success_at": last_success,
    }


# ---------------------------------------------------------------------------
# initAutoDream — sets up the closure-scoped runner (TS L122-273)
# ---------------------------------------------------------------------------


def init_auto_dream() -> None:
    """Call once at startup (from backgroundHousekeeping), or per-test in
    beforeEach for a fresh closure.
    """
    global _runner, _initialized

    _initialized = True

    last_session_scan_at = 0.0  # epoch ms, closure-scoped in TS

    async def _run_auto_dream(
        context: dict[str, Any],
        append_system_message: Callable[..., Any] | None = None,
    ) -> None:
        nonlocal last_session_scan_at
        global _is_running

        cfg = _get_config()
        force = _is_forced()

        if not force and not _is_gate_open():
            return

        # ---- Running guard (in-process mutual exclusion) ----
        # Catches the window between gate-pass and cross-process lock
        # acquisition. The cross-process lock handles multi-process races;
        # this flag handles the single-process case where two turns fire
        # in rapid succession.
        if _is_running:
            log_for_debugging(
                "[autoDream] skip — another dream is already in progress",
            )
            return

        # ---- Idle gate: only dream when user is idle ----
        if not force:
            try:
                from hare.services.auto_dream.config import (
                    AutoDreamConfig,
                )

                idle_minutes: int = AutoDreamConfig.min_idle_minutes
            except ImportError:
                idle_minutes = 0

            if idle_minutes > 0:
                # Extract last user message timestamp from context
                messages: list[dict[str, Any]]
                if isinstance(context, dict):
                    messages = list(context.get("messages") or [])
                else:
                    messages = list(getattr(context, "messages", []))

                last_user_at = _last_user_message_epoch_seconds(messages)
                if not _is_idle_long_enough(last_user_at, idle_minutes):
                    log_for_debugging(
                        f"[autoDream] skip — user active within "
                        f"{idle_minutes} min idle gate",
                    )
                    return

        # ---- Retry backoff gate ----
        # After consecutive failures, wait with exponential backoff before
        # retrying. Force bypasses this gate for manual /dream triggers.
        if _should_skip_retry_backoff(force):
            return

        # ---- Step 1: Read gate (time gate) ----
        try:
            from hare.services.auto_dream.consolidation_lock import (
                read_last_consolidated_at,
            )
        except ImportError:
            return

        try:
            last_at = await read_last_consolidated_at()
        except Exception as e:
            log_for_debugging(
                f"[autoDream] readLastConsolidatedAt failed: {e}",
            )
            return

        now_ms = time.time() * 1000.0
        hours_since = (now_ms - last_at) / 3_600_000.0
        if not force and hours_since < cfg["min_hours"]:
            return

        # ---- Step 2: Scan throttle ----
        since_scan_ms = now_ms - last_session_scan_at
        if not force and since_scan_ms < SESSION_SCAN_INTERVAL_MS:
            log_for_debugging(
                f"[autoDream] scan throttle — time-gate passed but last scan was "
                f"{round(since_scan_ms / 1000)}s ago",
            )
            return
        last_session_scan_at = now_ms

        # ---- Step 3: Session gate ----
        try:
            from hare.services.auto_dream.consolidation_lock import (
                list_sessions_touched_since,
            )
        except ImportError:
            return

        try:
            session_ids = await list_sessions_touched_since(last_at)
        except Exception as e:
            log_for_debugging(
                f"[autoDream] listSessionsTouchedSince failed: {e}",
            )
            return

        # Exclude the current session (its mtime is always recent)
        try:
            from hare.bootstrap.state import get_session_id

            current_session = get_session_id()
        except ImportError:
            current_session = ""
        session_ids = [sid for sid in session_ids if sid != current_session]

        if not force and len(session_ids) < cfg["min_sessions"]:
            log_for_debugging(
                f"[autoDream] skip — {len(session_ids)} sessions since last "
                f"consolidation, need {cfg['min_sessions']}",
            )
            return

        # ---- Step 4: Lock ----
        try:
            from hare.services.auto_dream.consolidation_lock import (
                try_acquire_consolidation_lock,
            )
        except ImportError:
            return

        prior_mtime: float | None
        if force:
            prior_mtime = last_at
        else:
            try:
                prior_mtime = await try_acquire_consolidation_lock()
            except Exception as e:
                log_for_debugging(
                    f"[autoDream] lock acquire failed: {e}",
                )
                return
            if prior_mtime is None:
                return

        # ---- Step 5: Fire ----
        _is_running = True
        log_for_debugging(
            f"[autoDream] firing — {hours_since:.1f}h since last, "
            f"{len(session_ids)} sessions to review",
        )

        try:
            from hare.services.analytics import log_event

            log_event("tengu_auto_dream_fired", {
                "hours_since": round(hours_since),
                "sessions_since": len(session_ids),
            })
        except Exception:
            pass

        # Resolve setAppState
        tool_use_context = (
            context.get("toolUseContext") or context.get("tool_use_context")
            if isinstance(context, dict)
            else getattr(context, "toolUseContext", None) or getattr(context, "tool_use_context", None)
        )
        set_app_state: Callable[[Any], None]
        if tool_use_context is not None:
            set_app_state = (
                getattr(tool_use_context, "set_app_state_for_tasks", None)
                or getattr(tool_use_context, "setAppStateForTasks", None)
                or getattr(tool_use_context, "set_app_state", None)
                or getattr(tool_use_context, "setAppState", None)
                or (lambda _: None)
            )
        else:
            set_app_state = lambda _: None

        # Register dream task
        try:
            from hare.tasks.dream_task import register_dream_task

            task_id = register_dream_task(set_app_state, {
                "sessions_reviewing": len(session_ids),
                "prior_mtime": prior_mtime,
            })
        except Exception:
            task_id = "dream-auto"

        # Pre-initialize tracking variables used in both success and error paths
        memory_root = ""
        result = None

        try:
            # Resolve memory root and transcript dir
            from hare.memdir.paths import get_auto_mem_path
            from hare.bootstrap.state import get_original_cwd
            from hare.utils.session_storage import get_project_dir

            import os as _path_os  # local alias to avoid shadowing

            memory_root = get_auto_mem_path().rstrip(_path_os.sep)
            transcript_dir = get_project_dir(get_original_cwd())

            # ---- Memory directory health check ----
            if not _check_memory_dir_health(memory_root):
                log_for_debugging(
                    "[autoDream] memory directory unhealthy, skipping consolidation"
                )
                _is_running = False
                return

            # Build extra context with tool constraints and session detail
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ms / 1000.0))
            last_str = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_at / 1000.0))
                if last_at > 0
                else "never"
            )

            extra = f"""

**Tool constraints for this run:** Bash is restricted to read-only commands (`ls`, `find`, `grep`, `cat`, `stat`, `wc`, `head`, `tail`, and similar). Anything that writes, redirects to a file, or modifies state will be denied. Plan your exploration with this in mind — no need to probe.

Consolidation gap: {hours_since:.1f} hours (last consolidation was {last_str}, now is {now_str}).
Thresholds required to fire: >= {cfg["min_hours"]}h since last consolidation, >= {cfg["min_sessions"]} modified sessions.

Sessions modified since last consolidation ({len(session_ids)}):
{chr(10).join(f'- {sid}' for sid in session_ids)}"""

            from hare.services.auto_dream.consolidation_prompt import (
                build_consolidation_prompt,
            )

            prompt = build_consolidation_prompt(memory_root, transcript_dir, extra)

            # Create user message
            from hare.utils.messages import create_user_message

            prompt_messages = [create_user_message(content=prompt)]

            # Create cache-safe params
            from hare.utils.forked_agent import (
                create_cache_safe_params,
                ForkedAgentParams,
                run_forked_agent,
            )

            cache_safe = create_cache_safe_params(context)

            # Create canUseTool whitelist (reuse extract_memories pattern)
            from hare.services.extract_memories.extract_memories import (
                _create_auto_mem_can_use_tool,
            )

            can_use = _create_auto_mem_can_use_tool(memory_root)

            # Create progress watcher
            on_message = _make_dream_progress_watcher(task_id, set_app_state)

            # Run the forked agent
            result = await run_forked_agent(
                ForkedAgentParams(
                    prompt_messages=prompt_messages,
                    cache_safe_params=cache_safe,
                    can_use_tool=can_use,
                    query_source="auto_dream",
                    fork_label="auto_dream",
                    skip_transcript=True,
                    on_message=on_message,
                )
            )

            # ---- Success path ----
            try:
                from hare.tasks.dream_task import complete_dream_task

                complete_dream_task(task_id, set_app_state)
            except Exception:
                pass

            # Inline completion summary in the main transcript
            if append_system_message is not None:
                try:
                    from hare.tasks.dream_task import is_dream_task

                    dream_state = None
                    if tool_use_context is not None:
                        get_app_state = (
                            getattr(tool_use_context, "get_app_state", None)
                            or getattr(tool_use_context, "getAppState", None)
                        )
                        if get_app_state is not None:
                            app_state = get_app_state()
                            if app_state is not None:
                                tasks = (
                                    app_state.get("tasks", {})
                                    if isinstance(app_state, dict)
                                    else getattr(app_state, "tasks", {})
                                )
                                dream_state = (
                                    tasks.get(task_id)
                                    if isinstance(tasks, dict)
                                    else getattr(tasks, task_id, None)
                                )

                    if dream_state is not None and is_dream_task(dream_state):
                        files_touched = (
                            dream_state.get("filesTouched", [])
                            if isinstance(dream_state, dict)
                            else getattr(dream_state, "filesTouched", [])
                        )
                        if files_touched:
                            # Create inline memory-saved-style message
                            names = ", ".join(
                                (
                                    f.split("/")[-1]
                                    if isinstance(f, str)
                                    else str(f)
                                )
                                for f in files_touched[:5]
                            )
                            suffix = (
                                f" (+{len(files_touched) - 5} more)"
                                if len(files_touched) > 5
                                else ""
                            )
                            append_system_message({
                                "type": "system",
                                "subtype": "memory_saved",
                                "verb": "Improved",
                                "message": {
                                    "content": f"Improved memory files: {names}{suffix}",
                                },
                            })
                except Exception:
                    pass

            # Log completion
            log_for_debugging(
                f"[autoDream] completed — cache: "
                f"read={result.total_usage.get('cache_read_input_tokens', 0)} "
                f"created={result.total_usage.get('cache_creation_input_tokens', 0)}",
            )
            try:
                from hare.services.analytics import log_event

                log_event("tengu_auto_dream_completed", {
                    "cache_read": result.total_usage.get("cache_read_input_tokens", 0),
                    "cache_created": result.total_usage.get(
                        "cache_creation_input_tokens", 0
                    ),
                    "output": result.total_usage.get("output_tokens", 0),
                    "sessions_reviewed": len(session_ids),
                })
            except Exception:
                pass

            # ---- Record to consolidation history ----
            _record_consolidation_attempt(
                memory_root,
                session_count=len(session_ids),
                hours_since=hours_since,
                success=True,
                cache_read_tokens=result.total_usage.get("cache_read_input_tokens", 0),
                cache_created_tokens=result.total_usage.get(
                    "cache_creation_input_tokens", 0
                ),
                output_tokens=result.total_usage.get("output_tokens", 0),
            )

            # ---- Reset retry state on success ----
            _update_retry_state(success=True)

            _is_running = False

        except Exception as e:
            # If the user aborted, don't double-rollback
            err_msg = str(e)
            if "aborted" in err_msg.lower() or "cancel" in err_msg.lower():
                log_for_debugging("[autoDream] aborted by user")
                _is_running = False
                return

            log_for_debugging(f"[autoDream] fork failed: {e}")
            try:
                from hare.services.analytics import log_event

                log_event("tengu_auto_dream_failed", {})
            except Exception:
                pass

            try:
                from hare.tasks.dream_task import fail_dream_task

                fail_dream_task(task_id, set_app_state)
            except Exception:
                pass

            # Rewind mtime so time-gate passes again. Scan throttle is the backoff.
            try:
                from hare.services.auto_dream.consolidation_lock import (
                    rollback_consolidation_lock,
                )

                await rollback_consolidation_lock(prior_mtime)
            except Exception:
                pass

            # ---- Record failure to consolidation history ----
            try:
                _record_consolidation_attempt(
                    memory_root,
                    session_count=len(session_ids),
                    hours_since=hours_since,
                    success=False,
                    error=str(e)[:500],
                )
            except Exception:
                pass

            # ---- Update retry state for backoff ----
            _update_retry_state(success=False)

            _is_running = False

    _runner = _run_auto_dream


# ---------------------------------------------------------------------------
# Entry point from stopHooks (TS L319-324)
# ---------------------------------------------------------------------------


async def execute_auto_dream(
    hook_context: dict[str, Any],
    append_system_message: Callable[..., Any] | None = None,
) -> None:
    """
    Fire-and-forget background analysis after each turn.

    No-op until init_auto_dream() has been called.
    Per-turn cost when enabled: one GB cache read + one stat.
    """
    if _runner is not None:
        await _runner(hook_context, append_system_message)


# ---------------------------------------------------------------------------
# Periodic background scheduling
# ---------------------------------------------------------------------------


async def schedule_background_auto_dream(
    context: dict[str, Any],
    append_system_message: Callable[..., Any] | None = None,
    interval_seconds: float = 600.0,
) -> asyncio.Task[None] | None:
    """Create a background asyncio task that periodically checks and fires
    auto-dream on the given interval.

    The task runs until cancelled. Each tick calls execute_auto_dream which
    internally gates (time, sessions, idle, lock, backoff) so this is safe
    to call frequently — most ticks will be no-ops.

    Returns the created asyncio.Task, or None if auto_dream is not initialized
    (no runner set).

    Args:
        context: The hook context dict to pass to execute_auto_dream.
        append_system_message: Optional callback for inline completion messages.
        interval_seconds: How often to check gates (default 10 minutes).
    """
    if _runner is None:
        log_for_debugging(
            "[autoDream] schedule_background_auto_dream: runner not initialized"
        )
        return None

    async def _periodic_tick() -> None:
        """Loop that fires execute_auto_dream on each interval tick."""
        tick = 0
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                tick += 1
                log_for_debugging(
                    f"[autoDream] periodic tick #{tick} "
                    f"(interval={interval_seconds:.0f}s)"
                )
                await execute_auto_dream(context, append_system_message)
            except asyncio.CancelledError:
                log_for_debugging(
                    f"[autoDream] periodic scheduler cancelled after {tick} ticks"
                )
                return
            except Exception as exc:
                log_for_debugging(
                    f"[autoDream] periodic tick #{tick} failed: {exc}"
                )
                # Continue looping — a single tick failure must not kill the
                # scheduler. The retry backoff in _run_auto_dream will
                # naturally throttle repeated failures.

    task = asyncio.create_task(_periodic_tick())
    log_for_debugging(
        f"[autoDream] started periodic scheduler (interval={interval_seconds:.0f}s)"
    )
    return task


def cancel_background_auto_dream(task: asyncio.Task[None] | None) -> bool:
    """Cancel a background auto-dream scheduler task.

    Returns True if the task was cancelled, False if task was None or already done.
    Safe to call with None (no-op).
    """
    if task is None:
        return False
    if task.done():
        return False
    task.cancel()
    log_for_debugging("[autoDream] cancelled periodic scheduler")
    return True


# ---------------------------------------------------------------------------
# Public helpers — inspect/control state (exposed for hooks, tests, debug)
# ---------------------------------------------------------------------------


def is_auto_dream_initialized() -> bool:
    """Check whether init_auto_dream() has been called.

    False before startup or after _reset_auto_dream().
    """
    return _initialized and _runner is not None


def is_auto_dream_running() -> bool:
    """Check whether a dream consolidation is currently in progress.

    True from gate-pass/lock-acquire through completion or error.
    Use this to avoid scheduling overlapping background work.
    """
    return _is_running


async def force_auto_dream(
    context: dict[str, Any],
    append_system_message: Callable[..., Any] | None = None,
) -> bool:
    """Force an auto-dream run for testing, bypassing all gates except
    the running guard and memory-dir precondition.

    Sets HARE_AUTO_DREAM_FORCE=1 in the env temporarily to override
    _is_forced(), then invokes the runner directly.

    Returns True if the dream was launched (or skipped via running guard),
    False if init_auto_dream() has not been called.
    """
    if _runner is None:
        return False

    prev = os.environ.get("HARE_AUTO_DREAM_FORCE", "")

    try:
        os.environ["HARE_AUTO_DREAM_FORCE"] = "1"
        await _runner(context, append_system_message)
    finally:
        if prev:
            os.environ["HARE_AUTO_DREAM_FORCE"] = prev
        else:
            os.environ.pop("HARE_AUTO_DREAM_FORCE", None)

    return True


def get_auto_dream_diagnostics() -> dict[str, Any]:
    """Return a snapshot of auto-dream state for diagnostics/debugging.

    Useful for health-check endpoints and integration test validation.
    """
    cfg = _get_config()

    # Collect retry state
    retry_info: dict[str, Any] = {
        "consecutive_failures": int(_retry_state["consecutive_failures"]),
        "backoff_remaining_seconds": round(_compute_retry_backoff_seconds(), 1),
        "next_retry_after": float(_retry_state["next_retry_after_epoch_seconds"]),
    }

    # Collect consolidation statistics if memory dir is reachable
    stats: dict[str, Any] = {}
    try:
        from hare.memdir.paths import get_auto_mem_path

        mem_root = get_auto_mem_path()
        stats = _compute_dream_statistics(mem_root)
    except Exception:
        stats = {"error": "memory directory not reachable"}

    return {
        "initialized": _initialized,
        "runner_set": _runner is not None,
        "is_running": _is_running,
        "config": {
            "min_hours": cfg["min_hours"],
            "min_sessions": cfg["min_sessions"],
        },
        "retry": retry_info,
        "consolidation_stats": stats,
    }


async def detect_stale_consolidation_lock() -> dict[str, Any]:
    """Check whether the consolidation lock file is held by a dead process.

    If a previous dream process crashed without cleaning up, the lock file
    persists with a stale PID. This function detects this condition so the
    next dream run can reclaim the lock.

    Returns a dict with:
      - stale: bool — True if the lock is held by a dead/missing process
      - holder_pid: int | None — the PID in the lock file body
      - lock_age_seconds: float — how long the lock file has existed
    """
    import os as _os

    try:
        from hare.services.auto_dream.consolidation_lock import _lock_path
    except ImportError:
        return {"stale": False, "holder_pid": None, "lock_age_seconds": 0.0}

    path = _lock_path()
    try:
        s = _os.stat(path)
        lock_age = time.time() - s.st_mtime
    except OSError:
        return {"stale": False, "holder_pid": None, "lock_age_seconds": 0.0}

    holder_pid: int | None = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if raw:
            holder_pid = int(raw)
    except (ValueError, OSError):
        pass

    if holder_pid is None:
        return {"stale": True, "holder_pid": None, "lock_age_seconds": lock_age}

    try:
        from hare.utils.generic_process_utils import is_process_running

        alive = is_process_running(holder_pid)
    except Exception:
        alive = False

    return {
        "stale": not alive,
        "holder_pid": holder_pid,
        "lock_age_seconds": round(lock_age, 1),
    }
