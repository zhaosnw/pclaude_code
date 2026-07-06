"""
Turn-end cleanup for computer use: auto-unhide apps and release lock after a turn.
Called from stop hooks (CHICAGO_MCP gate) and on interrupt.

Port of: src/utils/computerUse/cleanup.ts, computerUseLock.ts, escHotkey.ts
"""

from __future__ import annotations

import asyncio, json, os, signal, subprocess, sys, time
from pathlib import Path
from typing import Any, Callable

from hare.utils.debug import log_for_debugging

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_hidden: set[str] | None = None
_lock_held: bool = False
_lock_sid: str = ""
_esc_on: bool = False
_shutdown_unreg: Callable[[], None] | None = None

# ---------------------------------------------------------------------------
# File-based lock (cross-process — mirrors computerUseLock.ts)
# ---------------------------------------------------------------------------

def _lp() -> str:
    from hare.utils.env_utils import get_hare_config_home_dir
    return str(Path(get_hare_config_home_dir()) / "computer-use.lock")

def _rl() -> dict[str, Any] | None:
    try:
        d = json.loads(Path(_lp()).read_text("utf-8"))
        return d if isinstance(d, dict) and "sessionId" in d and "pid" in d else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

def _pa(pid: int) -> bool:
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False

def _ce(sid: str, pid: int) -> bool:
    p = Path(_lp()); p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, json.dumps({"sessionId": sid, "pid": pid,
                   "acquiredAt": int(time.time() * 1000)}).encode())
        os.close(fd); return True
    except FileExistsError:
        return False

def _ul() -> None:
    try:
        Path(_lp()).unlink(missing_ok=True)
    except OSError:
        pass

def is_lock_held_locally() -> bool:
    return _shutdown_unreg is not None

async def try_acquire_computer_use_lock() -> dict[str, Any]:
    from hare.bootstrap.state import get_session_id
    sid, pid = get_session_id(), os.getpid()
    def _try() -> dict[str, Any]:
        if _ce(sid, pid):                                 # fresh
            _rs(); return {"kind": "acquired", "fresh": True}
        ex = _rl()
        if not ex:                                         # corrupt → stale
            _ul()
            if _ce(sid, pid): _rs(); return {"kind": "acquired", "fresh": True}
            return {"kind": "blocked", "by": "unknown"}
        if ex.get("sessionId") == sid:
            return {"kind": "acquired", "fresh": False}     # re-entrant
        if _pa(ex.get("pid", 0)):
            return {"kind": "blocked", "by": ex.get("sessionId", "unknown")}
        log_for_debugging(f"Stale CU lock from {ex.get('sessionId')} (PID {ex.get('pid')})")
        _ul()
        if _ce(sid, pid): _rs(); return {"kind": "acquired", "fresh": True}
        return {"kind": "blocked", "by": (_rl() or {}).get("sessionId", "unknown")}
    return _try()

async def release_computer_use_lock() -> bool:
    global _shutdown_unreg
    _shutdown_unreg and _shutdown_unreg(); _shutdown_unreg = None
    from hare.bootstrap.state import get_session_id
    ex = _rl()
    if not ex or ex.get("sessionId") != get_session_id():
        return False
    _ul(); log_for_debugging("Released CU lock"); return True

async def check_computer_use_lock() -> dict[str, Any]:
    ex = _rl()
    if not ex: return {"kind": "free"}
    from hare.bootstrap.state import get_session_id
    if ex.get("sessionId") == get_session_id(): return {"kind": "held_by_self"}
    if _pa(ex.get("pid", 0)): return {"kind": "blocked", "by": ex.get("sessionId", "unknown")}
    _ul(); return {"kind": "free"}

def _rs() -> None:
    global _shutdown_unreg
    from hare.utils.cleanup_registry import register_cleanup
    _shutdown_unreg and _shutdown_unreg()
    _shutdown_unreg = register_cleanup(lambda: release_computer_use_lock())

# ---------------------------------------------------------------------------
# In-process lock
# ---------------------------------------------------------------------------

def is_lock_held() -> bool: return _lock_held
def get_locked_session_id() -> str: return _lock_sid

def acquire_lock(session_id: str) -> bool:
    global _lock_held, _lock_sid
    if _lock_held and _lock_sid != session_id: return False
    _lock_held, _lock_sid = True, session_id; return True

def release_lock(session_id: str) -> bool:
    global _lock_held, _lock_sid
    if _lock_sid != session_id: return False
    _lock_held, _lock_sid = False, ""; return True

# ---------------------------------------------------------------------------
# ESC hotkey (SIGINT handler — mirrors escHotkey.ts)
# ---------------------------------------------------------------------------

def register_esc_hotkey(on_escape: Callable[[], None]) -> bool:
    global _esc_on
    if _esc_on: return True
    try:
        signal.signal(signal.SIGINT, lambda *_: (log_for_debugging("[cu-esc] abort"), on_escape()))
        _esc_on = True; return True
    except (ValueError, OSError):
        return False

def unregister_esc_hotkey() -> None:
    global _esc_on
    if not _esc_on: return
    signal.signal(signal.SIGINT, signal.SIG_DFL); _esc_on = False

def notify_expected_escape() -> None: pass

# ---------------------------------------------------------------------------
# Hidden-apps tracking
# ---------------------------------------------------------------------------

def get_hidden_apps() -> set[str] | None: return _hidden

def set_hidden_apps(apps: set[str]) -> None:
    global _hidden; _hidden = apps

def prepare_for_action(
    allowlist_bundle_ids: list[str], display_id: int | None = None,
) -> list[str]:
    return []  # real hidden set from prepareDisplay; caller feeds to set_hidden_apps

# ---------------------------------------------------------------------------
# App unhiding (macOS AppleScript)
# ---------------------------------------------------------------------------

def _unhide_one(bid: str) -> bool:
    if sys.platform != "darwin": return False
    try:
        return subprocess.run(["osascript", "-e", (
            f'tell application "System Events"\n'
            f'  set p to first process whose bundle identifier is "{bid}"\n'
            f"  set visible of p to true\n  set frontmost of p to true\nend tell"
        )], capture_output=True, text=True, timeout=5).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False

async def _unhide_all() -> int:
    apps = _hidden
    if not apps: return 0
    bids = list(apps)
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _unhide_one, b) for b in bids]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=5.0,
        )
    except asyncio.TimeoutError:
        return 0
    n = sum(1 for r in results if r is True)
    log_for_debugging(f"CU unhid {n}/{len(bids)}")
    return n

# ---------------------------------------------------------------------------
# Force release (shutdown / cleanup registry)
# ---------------------------------------------------------------------------

async def force_release_all() -> None:
    global _hidden, _lock_held, _lock_sid
    try: await _unhide_all()
    except Exception: pass
    _hidden = None; unregister_esc_hotkey()
    try: await release_computer_use_lock()
    except Exception: pass
    _lock_held, _lock_sid = False, ""

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def cleanup_computer_use_after_turn(ctx: Any) -> None:
    """Auto-unhide apps and release lock after a turn. Subagent turns skipped."""
    global _hidden, _lock_held, _lock_sid

    if getattr(ctx, "agent_id", "") or "": return
    sid: str = getattr(ctx, "session_id", "") or ""

    # --- 1. Unhide apps (app state → canonical; module cache → fallback) ---
    app_st = None
    if callable(getattr(ctx, "get_app_state", None)):
        try: app_st = ctx.get_app_state()
        except Exception: pass

    hidden_st: set[str] | None = None
    if app_st is not None:
        cu = getattr(app_st, "computer_use_mcp_state", None)
        if cu is not None:
            hidden_st = getattr(cu, "hidden_during_turn", None)
    effective = hidden_st if hidden_st is not None else _hidden

    if effective:
        orig, _hidden = _hidden, effective
        try: await _unhide_all()
        except Exception: pass
        _hidden = orig
        if callable(getattr(ctx, "set_app_state", None)) and app_st is not None:
            try:
                from dataclasses import replace as _r
                ctx.set_app_state(lambda p: p if getattr(
                    getattr(p, "computer_use_mcp_state", None),
                    "hidden_during_turn", None) is None else _r(p,
                    computer_use_mcp_state=_r(getattr(p, "computer_use_mcp_state", None),
                    hidden_during_turn=None)))
            except Exception: pass
    _hidden = None

    # --- 2. Skip if lock was never held (zero-syscall gate) ---
    if not is_lock_held_locally(): return

    # --- 3. Unregister ESC hotkey (must not block lock release) ---
    try: unregister_esc_hotkey()
    except Exception: pass

    # --- 4. Release file-based lock ---
    released = False
    try: released = await release_computer_use_lock()
    except Exception: pass

    # --- 5. OS notification on successful release ---
    if released and callable(getattr(ctx, "send_os_notification", None)):
        try:
            ctx.send_os_notification({
                "message": "Claude is done using your computer",
                "notificationType": "computer_use_exit",
            })
        except Exception: pass

    # --- 6. Release in-memory lock ---
    if _lock_held and sid: release_lock(sid)
    elif _lock_held: _lock_held, _lock_sid = False, ""

    log_for_debugging(f"CU cleanup: done (released={released})")
