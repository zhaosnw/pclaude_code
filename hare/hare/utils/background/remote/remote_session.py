"""Remote session lifecycle with subprocess management and registry.

Port of: src/utils/background/remote/remoteSession.ts
"""

from __future__ import annotations

import asyncio, json, os, shutil, signal, subprocess, time, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

SessionStatus = Literal["starting", "running", "completed", "failed", "killed"]
PreconditionType = Literal[
    "not_logged_in", "no_remote_environment", "not_in_git_repo",
    "no_git_remote", "github_app_not_installed", "policy_blocked",
]
_MAX_CONCURRENT = 8
_STATE_DIR = Path.home() / ".hare" / "remote_sessions"

# ----------------------------------------------------------------- Data

@dataclass
class RemoteSessionPreconditionFailure:
    type: PreconditionType; message: str = ""

@dataclass
class RemoteSession:
    """Background remote session for teleport / remote agent runs."""
    session_id: str; command: str = ""
    status: SessionStatus = "starting"
    start_time: float = field(default_factory=time.time)
    title: str = ""; todo_list: list[dict[str, Any]] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    environment_id: str = ""; work_id: str = ""
    _abort: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list, repr=False)
    _proc: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    work_dir: str = ""; output_file: str = ""

    @property
    def elapsed_ms(self) -> float: return (time.time() - self.start_time) * 1000
    @property
    def is_active(self) -> bool: return self.status in ("starting", "running")
    @property
    def is_done(self) -> bool: return self.status in ("completed", "failed", "killed")
    @property
    def is_aborted(self) -> bool: return self._abort.is_set()
    @property
    def pid(self) -> int | None: return self._proc.pid if self._proc else None

    def _log(self, etype: str, msg: str = "", **m: Any) -> None:
        self.log.append({"type": etype, "message": msg, "ts": time.time(),
                          "elapsed": self.elapsed_ms, **m})

    def _bg(self, coro: Any) -> None: self._tasks.append(asyncio.create_task(coro))

    def read_output(self, offset: int = 0) -> tuple[str, int]:
        if not self.output_file: return ("", offset)
        try:
            with open(self.output_file, "r", errors="replace") as f: f.seek(offset); t = f.read()
            return (t, offset + len(t.encode("utf-8", errors="replace")))
        except OSError: return ("", offset)

# ---------------------------------------------------------------- Registry

_registry: dict[str, RemoteSession] = {}
_lock = asyncio.Lock()
def get_session(sid: str) -> RemoteSession | None: return _registry.get(sid)
async def _reg(s: RemoteSession) -> None:
    async with _lock: _registry[s.session_id] = s

async def _unreg(sid: str) -> None:
    async with _lock: _registry.pop(sid, None)

# -------------------------------------------------------- Work directory

def _work_dir(sid: str) -> Path:
    _STATE_DIR.mkdir(parents=True, exist_ok=True); return _STATE_DIR / sid

def _create_workdir(sid: str, cwd: str) -> str:
    src = cwd or os.getcwd(); base = _work_dir(sid)
    if os.path.isdir(os.path.join(src, ".git")):
        try:
            r = subprocess.run(["git", "-C", src, "worktree", "add", "--detach", str(base)],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0: return str(base)
        except Exception: pass
    try:
        base.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, base, dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
    except Exception: base.mkdir(parents=True, exist_ok=True)
    return str(base)

def _cleanup_workdir(sid: str, wd: str) -> None:
    if not wd or not os.path.isdir(wd): return
    try: subprocess.run(["git", "-C", os.getcwd(), "worktree", "remove", "--force", wd],
                        capture_output=True, text=True, timeout=10)
    except Exception: pass
    shutil.rmtree(wd, ignore_errors=True)

# -------------------------------------------------- Process management

def _spawn(session: RemoteSession, *, cwd: str) -> None:
    out = os.path.join(session.work_dir or cwd, f"{session.session_id}.output.log")
    session.output_file = out; Path(out).write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["CLAUDE_CODE_REMOTE_SESSION_ID"] = session.session_id
    env["CLAUDE_CODE_ENVIRONMENT_KIND"] = "remote"
    if session.environment_id: env["CLAUDE_CODE_ENVIRONMENT_ID"] = session.environment_id
    if session.work_id: env["CLAUDE_CODE_WORK_ID"] = session.work_id
    cmd = session.command or 'echo "no command specified"'
    session._proc = subprocess.Popen(
        cmd if isinstance(cmd, str) else cmd,
        cwd=session.work_dir or cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, shell=isinstance(cmd, str),
        preexec_fn=os.setsid if os.name != "nt" else None,
    )

async def _pump(session: RemoteSession) -> None:
    proc = session._proc
    if not proc or not session.output_file: return
    loop = asyncio.get_event_loop()
    async def _read(fh_attr: str, tag: str) -> None:
        fh = getattr(proc, fh_attr, None)
        if not fh: return
        with open(session.output_file, "a", buffering=1) as out:
            while True:
                try:
                    line = await loop.run_in_executor(None, fh.readline)
                    if not line: break
                    out.write(f"{tag}{line.decode('utf-8', errors='replace')}")
                except (OSError, ValueError): break
                except asyncio.CancelledError: break
    await asyncio.gather(_read("stdout", ""), _read("stderr", "[stderr] "),
                         return_exceptions=True)

async def _kill_proc(session: RemoteSession, reason: str = "killed") -> None:
    session._abort.set(); proc = session._proc
    if not proc or proc.poll() is not None: return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM); await asyncio.sleep(0.5)
            if proc.poll() is None: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.terminate(); await asyncio.sleep(0.5)
            if proc.poll() is None: proc.kill()
    except (ProcessLookupError, OSError): pass
    session.status = "killed"
    session._log("process_exit", f"Killed: {reason}", exit_code=proc.returncode)

async def abort_remote_session(session: RemoteSession) -> None:
    if not session.is_active: return
    session._abort.set(); session._log("abort")
    if session._proc and session._proc.poll() is None:
        try:
            if os.name != "nt": os.killpg(os.getpgid(session._proc.pid), signal.SIGINT)
            else: session._proc.send_signal(getattr(signal, "CTRL_C_EVENT", signal.SIGINT))
        except (ProcessLookupError, OSError): pass
    for t in session._tasks:
        if not t.done(): t.cancel()

# --------------------------------------------------------- Async loops

async def _heartbeat(session: RemoteSession, cb: Callable, iv: float) -> None:
    try:
        while session.is_active and not session.is_aborted:
            await asyncio.sleep(iv)
            if not session.is_active: break
            try: r = cb(session); await r if asyncio.iscoroutine(r) else None
            except Exception as e: session._log("error", str(e))
    except asyncio.CancelledError: pass

async def _timeout(session: RemoteSession, seconds: float) -> None:
    try:
        await asyncio.sleep(seconds)
        if session.is_active and not session.is_aborted:
            await _kill_proc(session, reason="timeout")
    except asyncio.CancelledError: pass

# ------------------------------------------------------------ Persistence

def _sp(sid: str) -> Path: return _work_dir(sid) / "state.json"

def save_session_state(session: RemoteSession) -> bool:
    try:
        p = _sp(session.session_id); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "session_id": session.session_id, "command": session.command,
            "status": session.status, "start_time": session.start_time,
            "title": session.title, "log": session.log, "metadata": session.metadata,
            "environment_id": session.environment_id, "work_id": session.work_id,
            "work_dir": session.work_dir, "output_file": session.output_file,
        }, indent=2), encoding="utf-8"); return True
    except (OSError, TypeError): return False

def restore_session_state(sid: str) -> RemoteSession | None:
    p = _sp(sid)
    if not p.exists(): return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        s = RemoteSession(session_id=sid, command=d.get("command", ""),
            status="failed" if d.get("status") in ("starting","running") else d.get("status","failed"),
            start_time=d.get("start_time", time.time()), title=d.get("title", ""),
            log=d.get("log", []), metadata=d.get("metadata", {}),
            environment_id=d.get("environment_id", ""), work_id=d.get("work_id", ""),
            work_dir=d.get("work_dir", ""), output_file=d.get("output_file", ""))
        return s
    except (json.JSONDecodeError, OSError, TypeError, KeyError): return None

# ------------------------------------------------------------- Eligibility

def _has_remote_env() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("CLAUDE_AI_ACCESS_TOKEN"))

def _has_remote(cwd: str) -> bool:
    try:
        r = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception: return False

async def _has_github_app(cwd: str) -> bool:
    try:
        r = subprocess.run(["git", "-C", cwd, "config", "--local", "--get-regexp", "claude"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception: return False

async def check_remote_session_eligibility(
    *, skip_bundle: bool = False,
) -> list[RemoteSessionPreconditionFailure]:
    """Return list of failed preconditions. Empty == eligible."""
    f: list[RemoteSessionPreconditionFailure] = []
    if os.environ.get("CCR_POLICY_BLOCK_ALLOW_REMOTE_SESSIONS") == "1":
        return [RemoteSessionPreconditionFailure("policy_blocked", "Blocked by org policy.")]
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        f.append(RemoteSessionPreconditionFailure("not_logged_in", "Login required."))
    if not _has_remote_env():
        f.append(RemoteSessionPreconditionFailure("no_remote_environment", "Run /remote-setup."))
    cwd = os.getcwd()
    if not os.path.isdir(os.path.join(cwd, ".git")):
        f.append(RemoteSessionPreconditionFailure("not_in_git_repo", "Requires a git repo."))
        return f
    if not (skip_bundle or os.environ.get("CCR_ENABLE_BUNDLE") == "1"):
        if not _has_remote(cwd):
            f.append(RemoteSessionPreconditionFailure("no_git_remote", "No git remote."))
        elif not await _has_github_app(cwd):
            f.append(RemoteSessionPreconditionFailure("github_app_not_installed", "GitHub App not installed."))
    return f

# --------------------------------------------------------- Start / stop

def _set_env(sid: str) -> None:
    os.environ["CLAUDE_CODE_REMOTE_SESSION_ID"] = sid
    os.environ["CLAUDE_CODE_ENVIRONMENT_KIND"] = "remote"

def _clear_env() -> None:
    for k in ("CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_ENVIRONMENT_KIND"): os.environ.pop(k, None)

async def start_remote_session(
    opts: dict[str, Any], *, heartbeat_interval: float = 30.0,
) -> RemoteSession:
    """Start a new remote background session."""
    sid = opts.get("session_id") or f"remote-{uuid.uuid4().hex[:12]}"
    command, cwd = opts.get("command", ""), opts.get("cwd", os.getcwd())

    # Evict oldest if at capacity
    active = sorted([s for s in _registry.values() if s.is_active], key=lambda s: s.start_time)
    for s in active[:len(active) - _MAX_CONCURRENT + 1]:
        await _kill_proc(s, reason="concurrency")
        await _unreg(s.session_id)

    session = RemoteSession(
        session_id=sid, command=command,
        title=opts.get("title", command[:80] if command else ""),
        environment_id=opts.get("environment_id", ""),
        work_id=opts.get("work_id", ""), status="starting",
    )
    session.work_dir = _create_workdir(sid, cwd)
    session._log("workdir_created", path=session.work_dir)
    _set_env(sid); await _reg(session)

    if opts.get("auto_spawn", bool(command)) and command:
        _spawn(session, cwd=cwd)
        session._log("session_start", pid=session._proc.pid if session._proc else None)
        session._bg(_pump(session))
        async def _on_exit() -> None:
            if not session._proc: return
            loop = asyncio.get_event_loop()
            rc = await loop.run_in_executor(None, session._proc.wait)
            if session.is_active: session.status = "completed" if rc == 0 else "failed"
            session._log("process_exit", exit_code=rc)
        session._bg(_on_exit())

    session.status = "running"

    cb = opts.get("on_heartbeat")
    if cb and callable(cb): session._bg(_heartbeat(session, cb, heartbeat_interval))

    ts = opts.get("timeout_seconds", 3600.0)
    if ts > 0: session._bg(_timeout(session, ts))

    save_session_state(session)
    return session


async def stop_remote_session(session: RemoteSession) -> None:
    """Tear down: cancel tasks, kill process, clean work dir, persist."""
    if not session.is_active: return
    for t in session._tasks:
        if not t.done(): t.cancel()
        try: await t
        except asyncio.CancelledError: pass
    session._tasks.clear()

    if session._proc and session._proc.poll() is None:
        await _kill_proc(session, reason="session_stopped")

    if session.is_active: session.status = "killed" if session.is_aborted else "completed"
    if session._proc and session.status != "killed":
        rc = session._proc.poll()
        if rc is not None and rc != 0: session.status = "failed"

    session._log("session_stop", status=session.status, elapsed_ms=session.elapsed_ms)
    if not session.metadata.get("keep_work_dir"):
        _cleanup_workdir(session.session_id, session.work_dir)
        session._log("workdir_cleaned")

    save_session_state(session)
    _clear_env()
    await _unreg(session.session_id)
