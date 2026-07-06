"""
Wrap spawned subprocess with timeout, backgrounding, and TaskOutput integration.

Port of: src/utils/ShellCommand.ts (async Python variant; tree-kill → process group kill stub).
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from hare.utils.task.disk_output import (
    MAX_TASK_OUTPUT_BYTES,
    MAX_TASK_OUTPUT_BYTES_DISPLAY,
)
from hare.utils.task.task_output import TaskOutput

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIGKILL_CODE = 137
SIGTERM_CODE = 143
SIZE_WATCHDOG_INTERVAL_MS = 5_000

# Max timeout: 2 hours. Prevents spawns with unreasonably large timeouts.
MAX_TIMEOUT_MS = 2 * 60 * 60 * 1000

# Grace period between SIGTERM and SIGKILL when killing a process tree.
_KILL_GRACE_PERIOD_MS = 2_000

# Default for unbounded background output (file mode).
_DEFAULT_MAX_OUTPUT_BYTES = MAX_TASK_OUTPUT_BYTES

# ---------------------------------------------------------------------------
# Platform awareness
# ---------------------------------------------------------------------------

_IS_WINDOWS = os.name == "nt"


def _signal_code_sigkill() -> int:
    """Return the conventional SIGKILL exit code for this platform."""
    return SIGKILL_CODE  # 128 + 9


def _signal_code_sigterm() -> int:
    """Return the conventional SIGTERM exit code for this platform."""
    return SIGTERM_CODE  # 128 + 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prepend_stderr(prefix: str, stderr: str) -> str:
    """Prepend a prefix line to stderr text, separated by a newline."""
    return f"{prefix}\n{stderr}" if stderr else prefix


def _format_duration_ms(ms: int) -> str:
    """Human-readable duration string from milliseconds."""
    if ms >= 60_000:
        return f"{ms // 60_000}m"
    if ms >= 1_000:
        return f"{ms // 1_000}s"
    return f"{ms}ms"


def _now_ms() -> int:
    """Current time in milliseconds (for elapsed tracking)."""
    return int(time.time() * 1000)


def _clip_timeout(timeout_ms: int) -> int:
    """Ensure timeout stays within sane bounds."""
    if timeout_ms <= 0:
        return 120_000
    if timeout_ms > MAX_TIMEOUT_MS:
        return MAX_TIMEOUT_MS
    return timeout_ms


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class ExecResult:
    """Result returned by a shell command execution."""

    stdout: str
    stderr: str
    code: int
    interrupted: bool
    # Background tracking
    background_task_id: str | None = None
    backgrounded_by_user: bool | None = None
    assistant_auto_backgrounded: bool | None = None
    # File-backed output
    output_file_path: str | None = None
    output_file_size: int | None = None
    output_task_id: str | None = None
    # Pre-spawn failure
    pre_spawn_error: str | None = None
    # Timing
    duration_ms: int | None = None
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        """True when the command exited with code 0 and was not interrupted."""
        return self.code == 0 and not self.interrupted and not self.pre_spawn_error


# ---------------------------------------------------------------------------
# Stream drain helper
# ---------------------------------------------------------------------------


async def _drain_stream(
    stream: asyncio.StreamReader | None, task_output: TaskOutput, is_stderr: bool
) -> None:
    """Read all bytes from *stream* and write them into *task_output*."""
    if stream is None:
        return
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            break
        s = chunk.decode("utf-8", errors="replace")
        if is_stderr:
            task_output.write_stderr(s)
        else:
            task_output.write_stdout(s)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ShellCommand(Protocol):
    """Interface that every shell-command wrapper must satisfy."""

    background: Callable[[str], bool]
    result: Any
    kill: Callable[[], None]
    status: Literal["running", "backgrounded", "completed", "killed"]
    cleanup: Callable[[], None]
    on_timeout: Any
    task_output: TaskOutput


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class ShellCommandImpl:
    """Async subprocess wrapper — mirrors ShellCommandImpl in TS.

    Lifecycle
    ---------
    1. Created with a running ``asyncio.subprocess.Process``.
    2. ``_drive()`` coroutine owns the lifetime: drains streams, enforces
       timeout, and resolves ``result``.
    3. While ``status == "running"`` the caller may ``background()`` the command
       (timeout cancels, output continues to a file) or ``kill()`` it.
    4. Once the future ``result`` resolves, the command is considered settled
       and ``cleanup()`` tears down remaining resources (watchdog tasks, etc.).
    """

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        timeout_ms: int,
        task_output: TaskOutput,
        *,
        should_auto_background: bool = False,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
        command: str = "",
        cwd: str = "",
    ) -> None:
        self._proc = proc
        self._timeout_ms = _clip_timeout(timeout_ms)
        self.task_output = task_output
        self._should_auto_background = should_auto_background
        self._max_output_bytes = max_output_bytes
        self._command = command
        self._cwd = cwd
        self._start_ms = _now_ms()

        # ---- state ----
        self._status: Literal["running", "backgrounded", "completed", "killed"] = (
            "running"
        )
        self._background_task_id: str | None = None
        self._backgrounded_by_user: bool = False
        self._auto_backgrounded: bool = False

        # ---- timeout callback (only meaningful when auto-background is on) ----
        self._on_timeout_cb: Callable[[Callable[[str], bool], None], None] | None = None

        # ---- optional size watchdog (background file mode) ----
        self._watchdog: asyncio.Task[None] | None = None
        self._killed_for_size = False

        # ---- result future ----
        self._result_fut: asyncio.Future[ExecResult] = asyncio.Future()

        # ---- timeout registration callback (set by caller) ----
        self.on_timeout: (
            Callable[[Callable[[Callable[[str], bool], None], None], None], None] | None
        ) = None
        if should_auto_background:

            def _ot(cb: Callable[[Callable[[str], bool], None], None]) -> None:
                self._on_timeout_cb = cb

            self.on_timeout = _ot

        # ---- start the lifecycle coroutine ----
        self._main = asyncio.create_task(self._drive())

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def status(self) -> Literal["running", "backgrounded", "completed", "killed"]:
        return self._status

    @property
    def result(self) -> asyncio.Future[ExecResult]:
        return self._result_fut

    @property
    def pid(self) -> int | None:
        """Expose the OS PID of the spawned process."""
        return self._proc.pid

    @property
    def command(self) -> str:
        """The command string that was executed."""
        return self._command

    @property
    def cwd(self) -> str:
        """The working directory in which the command was started."""
        return self._cwd

    @property
    def is_running(self) -> bool:
        """True while the command has not settled (running or backgrounded)."""
        return self._status in ("running", "backgrounded")

    @property
    def elapsed_ms(self) -> int:
        """Wall-clock time since this wrapper was created."""
        return _now_ms() - self._start_ms

    @property
    def is_backgrounded(self) -> bool:
        """True when the command has been moved to background."""
        return self._status == "backgrounded"

    @property
    def background_task_id(self) -> str | None:
        """Task ID assigned on background(), if any."""
        return self._background_task_id

    # ------------------------------------------------------------------
    # Lifecycle coroutine
    # ------------------------------------------------------------------

    async def _drive(self) -> None:
        """Main lifecycle: drain streams, enforce timeout, collect result."""
        out_t = asyncio.create_task(
            _drain_stream(self._proc.stdout, self.task_output, False)
        )
        err_t = asyncio.create_task(
            _drain_stream(self._proc.stderr, self.task_output, True)
        )

        async def _timeout() -> None:
            await asyncio.sleep(self._timeout_ms / 1000.0)
            if self._status != "running":
                return
            if self._should_auto_background and self._on_timeout_cb is not None:
                # When auto-background is on, signal the caller to decide.
                self._on_timeout_cb(self.background)
            else:
                # No background fallback — kill the process tree.
                await self._do_sigterm_then_sigkill()

        timer = asyncio.create_task(_timeout())
        try:
            code = await self._proc.wait()
        finally:
            timer.cancel()
            await asyncio.gather(out_t, err_t, return_exceptions=True)

        if self._status in ("running", "backgrounded"):
            self._status = "completed"

        duration_ms = _now_ms() - self._start_ms
        res = self._build_final_result(code, duration_ms)
        if not self._result_fut.done():
            self._result_fut.set_result(res)

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _build_final_result(self, code: int | None, duration_ms: int) -> ExecResult:
        """Build the ExecResult dict from the final state."""
        stdout = ""  # will be repopulated by caller via task_output
        stderr = ""
        exit_code = code if code is not None else 1

        # On POSIX a process killed by signal N returns -N from proc.wait().
        # Normalize both representations: negative signal and 128+N convention.
        _is_sigkill = (
            exit_code == -signal.SIGKILL
            or exit_code == _signal_code_sigkill()
        )
        _is_sigterm = (
            exit_code == -signal.SIGTERM
            or exit_code == _signal_code_sigterm()
        )
        interrupted = _is_sigkill or _is_sigterm or (exit_code < 0)
        timed_out = _is_sigterm

        # Use the 128+N convention for the stored code when killed by signal.
        display_code = exit_code
        if exit_code == -signal.SIGKILL:
            display_code = _signal_code_sigkill()
        elif exit_code == -signal.SIGTERM:
            display_code = _signal_code_sigterm()

        res = ExecResult(
            code=int(display_code),
            stdout=stdout,
            stderr=stderr,
            interrupted=interrupted,
            background_task_id=self._background_task_id,
            backgrounded_by_user=self._backgrounded_by_user,
            assistant_auto_backgrounded=self._auto_backgrounded,
            output_task_id=self.task_output.task_id,
            output_file_path=self.task_output.path
            if self.task_output.stdout_to_file
            else None,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )

        # Attach reason messages to stderr when we killed the process.
        if self._killed_for_size:
            res.stderr = _prepend_stderr(
                f"Background command killed: output file exceeded {MAX_TASK_OUTPUT_BYTES_DISPLAY}",
                res.stderr,
            )
        elif _is_sigterm:
            res.stderr = _prepend_stderr(
                f"Command timed out after {_format_duration_ms(self._timeout_ms)}",
                res.stderr,
            )
        elif interrupted:
            res.stderr = _prepend_stderr("Command was interrupted", res.stderr or "")

        return res

    # ------------------------------------------------------------------
    # Killing
    # ------------------------------------------------------------------

    async def _do_sigterm_then_sigkill(self) -> None:
        """Graceful kill: SIGTERM, short grace, then SIGKILL the process group.

        Mirrors the TS tree-kill strategy: first try SIGTERM to give child
        processes a chance to clean up; if the process is still alive after a
        brief wait, escalate to SIGKILL.
        """
        if self._status not in ("running", "backgrounded"):
            return

        pid = self._proc.pid
        if pid is None:
            return

        self._status = "killed"

        try:
            self._send_signal_to_tree(signal.SIGTERM)
        except ProcessLookupError:
            pass

        # Wait briefly for graceful exit.
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=_KILL_GRACE_PERIOD_MS / 1000.0)
        except asyncio.TimeoutError:
            # Process still alive — escalate.
            try:
                self._send_signal_to_tree(signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def _do_kill(self, code: int) -> None:
        """Immediate forced kill (legacy path — prefer _do_sigterm_then_sigkill)."""
        self._status = "killed"
        if self._proc.pid:
            try:
                self._send_signal_to_tree(signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not self._result_fut.done():
            self._result_fut.set_result(
                ExecResult(
                    stdout="",
                    stderr="",
                    code=code,
                    interrupted=True,
                    duration_ms=self.elapsed_ms,
                )
            )

    def _send_signal_to_tree(self, sig: int) -> None:
        """Send *sig* to the process group if possible, else just the subprocess.

        On POSIX: use ``os.killpg(pgid, sig)`` if the process was created with
        ``start_new_session=True`` (which gives the child its own process group).
        Falls back to ``self._proc.send_signal(sig)``.
        """
        if _IS_WINDOWS:
            # Windows has no signals; terminate/kill the process tree.
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            return

        pid = self._proc.pid
        if pid is None:
            return

        # Try process-group kill first.  The spawner is expected to have set
        # ``start_new_session=True`` so that the child becomes its own process
        # group leader.  If the caller did not do that, the pgid equals the
        # parent's pgid and we must NOT kill the wrong group.
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return

        # Only killpg when the child IS the process-group leader (pgid == pid).
        if pgid == pid:
            try:
                os.killpg(pgid, sig)
                return
            except ProcessLookupError:
                return

        # Fallback: signal just the immediate child.
        try:
            self._proc.send_signal(sig)
        except ProcessLookupError:
            pass

    # ------------------------------------------------------------------
    # Backgrounding
    # ------------------------------------------------------------------

    def background(self, task_id: str) -> bool:
        """Move a running command to the background.

        When called the timeout is cancelled and output continues streaming to
        the file underlying ``task_output`` (if ``stdout_to_file`` is True).
        Returns ``True`` on success; ``False`` when status is no longer
        ``"running"``.
        """
        if self._status != "running":
            return False
        self._background_task_id = task_id
        self._backgrounded_by_user = True
        self._status = "backgrounded"
        if self.task_output.stdout_to_file:
            self._watchdog = asyncio.create_task(self._size_watchdog())
        return True

    def _auto_background(self, task_id: str) -> bool:
        """Background triggered automatically (not by the user)."""
        if self._status != "running":
            return False
        self._background_task_id = task_id
        self._auto_backgrounded = True
        self._status = "backgrounded"
        if self.task_output.stdout_to_file:
            self._watchdog = asyncio.create_task(self._size_watchdog())
        return True

    async def _size_watchdog(self) -> None:
        """Monitor the on-disk output file size when backgrounded.

        If the file exceeds ``_max_output_bytes`` the process is killed to
        prevent runaway disk usage.
        """
        path = Path(self.task_output.path)
        while self._status == "backgrounded":
            await asyncio.sleep(SIZE_WATCHDOG_INTERVAL_MS / 1000.0)
            try:
                sz = path.stat().st_size
            except OSError:
                continue
            if sz > self._max_output_bytes and self._status == "backgrounded":
                self._killed_for_size = True
                try:
                    self._send_signal_to_tree(signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return

    # ------------------------------------------------------------------
    # Public control methods
    # ------------------------------------------------------------------

    def kill(self) -> None:
        """Immediately kill the process tree (SIGKILL). Fire-and-forget."""
        asyncio.create_task(self._do_kill(_signal_code_sigkill()))

    def interrupt(self) -> None:
        """Send SIGTERM (graceful) to the process tree. Fire-and-forget.

        Unlike ``kill()`` this gives the process a chance to clean up.
        On platforms without signals it falls back to ``kill()``.
        """
        asyncio.create_task(self._do_sigterm_then_sigkill())

    def cleanup(self) -> None:
        """Cancel background tasks (watchdog, etc.). Safe to call multiple times."""
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None

    # ------------------------------------------------------------------
    # Async helpers
    # ------------------------------------------------------------------

    async def wait_for_result(self) -> ExecResult:
        """Await the final ``ExecResult``, like ``await sc.result`` but typed."""
        return await self._result_fut

    async def wait_for(
        self,
        extra_timeout_ms: int | None = None,
    ) -> ExecResult:
        """Wait for the command to settle, with an optional additional timeout.

        If *extra_timeout_ms* is supplied, raises ``asyncio.TimeoutError`` when
        the result is not available within that window.
        """
        coro = asyncio.shield(self._result_fut)
        if extra_timeout_ms is not None:
            coro = asyncio.wait_for(coro, timeout=extra_timeout_ms / 1000.0)
        return await coro

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> ShellCommandImpl:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.cleanup()
        if self.is_running:
            self.kill()


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


async def wrap_spawn(
    proc: asyncio.subprocess.Process,
    _abort_event: asyncio.Event,
    timeout_ms: int,
    task_output: TaskOutput,
    should_auto_background: bool = False,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    command: str = "",
    cwd: str = "",
) -> ShellCommandImpl:
    """Wrap a freshly-spawned subprocess into a ``ShellCommandImpl``.

    The *proc* should already be running.  *abort_event* is consumed but not
    wired inside ``ShellCommandImpl`` — the caller may optionally cancel the
    internal ``_main`` task when the event fires.
    """
    del _abort_event
    return ShellCommandImpl(
        proc,
        timeout_ms,
        task_output,
        should_auto_background=should_auto_background,
        max_output_bytes=max_output_bytes,
        command=command,
        cwd=cwd,
    )


async def run_shell_command(
    command: str,
    *,
    cwd: str = "",
    timeout_ms: int = 120_000,
    stdout_to_file: bool = False,
    env: dict[str, str] | None = None,
    shell: str = "",
) -> ExecResult:
    """High-level convenience: spawn a shell command and wait for the result.

    Parameters
    ----------
    command : str
        The shell command to execute.
    cwd : str
        Working directory. Defaults to ``os.getcwd()``.
    timeout_ms : int
        Timeout in milliseconds (clamped to 2 hours max).
    stdout_to_file : bool
        If True, output is written to a file and the result carries
        ``output_file_path``.
    env : dict | None
        Extra environment variables merged on top of the current process env.
    shell : str
        Explicit shell path. Default: auto-detect (bash or zsh on POSIX).

    Returns
    -------
    ExecResult
    """
    task_id = _new_task_id()
    out_path = Path(f"/tmp/hare-task-{task_id}.log") if stdout_to_file else None
    task_output = TaskOutput(
        task_id,
        on_progress=None,
        stdout_to_file=stdout_to_file,
        output_path=out_path,
    )

    # Resolve shell
    resolved_shell = shell or _resolve_default_shell()
    resolved_cwd = cwd or os.getcwd()

    proc_env = {**os.environ}
    if env:
        proc_env.update(env)

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved_shell,
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=resolved_cwd,
            env=proc_env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return ExecResult(
            stdout="",
            stderr=f"Shell not found: {resolved_shell}",
            code=1,
            interrupted=False,
            pre_spawn_error=f"Shell not found: {resolved_shell}",
        )
    except Exception as exc:
        return ExecResult(
            stdout="",
            stderr=str(exc),
            code=1,
            interrupted=False,
            pre_spawn_error=str(exc),
        )

    impl = ShellCommandImpl(
        proc,
        timeout_ms,
        task_output,
        command=command,
        cwd=resolved_cwd,
    )

    exec_result = await impl.wait_for_result()
    impl.cleanup()

    # The stdout/stderr were empty at build time because they needed await.
    # Rebuild with actual output.
    exec_result.stdout = await task_output.get_stdout()
    exec_result.stderr = task_output.get_stderr()

    # If output went to a file, note its size.
    if stdout_to_file and out_path and out_path.exists():
        exec_result.output_file_size = out_path.stat().st_size

    return exec_result


async def run_background_command(
    command: str,
    task_id: str,
    *,
    cwd: str = "",
    timeout_ms: int = 0,
    env: dict[str, str] | None = None,
    shell: str = "",
) -> ShellCommandImpl:
    """Spawn a command and immediately background it under *task_id*.

    This creates a ``ShellCommandImpl`` with auto-background disabled (the
    caller already wants it backgrounded) and transitions it to ``"backgrounded"``
    status straight away.

    The timeout is set to 0 (disabled) by default for background commands.
    """
    resolved_shell = shell or _resolve_default_shell()
    resolved_cwd = cwd or os.getcwd()

    proc_env = {**os.environ}
    if env:
        proc_env.update(env)

    out_path = Path(f"/tmp/hare-task-{task_id}.log")
    task_output = TaskOutput(
        task_id,
        on_progress=None,
        stdout_to_file=True,
        output_path=out_path,
    )

    proc = await asyncio.create_subprocess_exec(
        resolved_shell,
        "-c",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=resolved_cwd,
        env=proc_env,
        start_new_session=True,
    )

    impl = ShellCommandImpl(
        proc,
        timeout_ms if timeout_ms > 0 else MAX_TIMEOUT_MS,  # background commands get max timeout
        task_output,
        command=command,
        cwd=resolved_cwd,
    )
    impl.background(task_id)
    return impl


# ---------------------------------------------------------------------------
# Synthetic commands (pre-resolved results)
# ---------------------------------------------------------------------------


def create_aborted_command(
    background_task_id: str | None = None,
    *,
    stderr: str | None = None,
    code: int | None = None,
) -> Any:
    """Return a ShellCommand-shaped object that behaves as already-killed.

    Useful when the tool permission check decides to abort the command
    before any process was spawned.
    """
    _to = TaskOutput(_new_task_id(), None)
    fut: asyncio.Future[ExecResult] = asyncio.Future()
    fut.set_result(
        ExecResult(
            code=code if code is not None else 145,
            stdout="",
            stderr=stderr or "Command aborted before execution",
            interrupted=True,
            background_task_id=background_task_id,
        )
    )
    _bg_task_id = background_task_id

    class _Aborted:
        status = "killed"
        result = fut
        task_output = _to
        on_timeout = None
        pid = None
        command = ""
        cwd = ""
        is_running = False
        elapsed_ms = 0
        is_backgrounded = False
        background_task_id = _bg_task_id

        def background(self, _task_id: str) -> bool:
            return False

        def kill(self) -> None:
            pass

        def interrupt(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

        async def wait_for_result(self) -> ExecResult:
            return fut.result()

        async def wait_for(self, _extra_timeout_ms: int | None = None) -> ExecResult:
            return fut.result()

    return _Aborted()


def create_failed_command(pre_spawn_error: str) -> Any:
    """Return a ShellCommand-shaped object representing a spawn failure.

    The *pre_spawn_error* text becomes both the ``stderr`` and
    ``pre_spawn_error`` field of the wrapped ``ExecResult``.
    """
    _to = TaskOutput(_new_task_id(), None)
    fut: asyncio.Future[ExecResult] = asyncio.Future()
    fut.set_result(
        ExecResult(
            code=1,
            stdout="",
            stderr=pre_spawn_error,
            interrupted=False,
            pre_spawn_error=pre_spawn_error,
        )
    )

    class _Failed:
        status = "completed"
        result = fut
        task_output = _to
        on_timeout = None
        pid = None
        command = ""
        cwd = ""
        is_running = False
        elapsed_ms = 0
        is_backgrounded = False
        background_task_id = None

        def background(self, _task_id: str) -> bool:
            return False

        def kill(self) -> None:
            pass

        def interrupt(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

        async def wait_for_result(self) -> ExecResult:
            return fut.result()

        async def wait_for(self, _extra_timeout_ms: int | None = None) -> ExecResult:
            return fut.result()

    return _Failed()


def create_pre_running_command(
    command: str,
    *,
    cwd: str = "",
    task_id: str | None = None,
) -> Any:
    """Return a ShellCommand-shaped object for a command that has not yet
    been spawned (status is ``"pending"`` / not yet running).  This is a
    lightweight placeholder until the real ``ShellCommandImpl`` is created.

    Callers can later replace it with a real impl once the process starts.
    """
    effective_task_id = task_id or _new_task_id()
    _to = TaskOutput(effective_task_id, None)
    _cmd = command
    _dir = cwd or os.getcwd()

    class _PreRunning:
        status = "running"
        task_output = _to
        on_timeout = None
        pid = None
        command = _cmd
        cwd = _dir
        is_running = True
        elapsed_ms = 0
        is_backgrounded = False
        background_task_id = None
        result: asyncio.Future[ExecResult] = asyncio.Future()

        def background(self, _task_id: str) -> bool:
            return False

        def kill(self) -> None:
            pass

        def interrupt(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

        async def wait_for_result(self) -> ExecResult:
            return await self.result

        async def wait_for(self, _extra_timeout_ms: int | None = None) -> ExecResult:
            return await self.result

    return _PreRunning()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_task_id() -> str:
    """Generate a unique short task ID suitable for file paths and logging."""
    return uuid.uuid4().hex[:12]


def _resolve_default_shell() -> str:
    """Best-effort shell resolution, falling back to ``/bin/sh``."""
    if _IS_WINDOWS:
        import shutil

        pwsh = shutil.which("powershell") or shutil.which("pwsh")
        return pwsh or "powershell"

    shell = os.environ.get("SHELL", "")
    if shell and os.access(shell, os.X_OK):
        return shell

    for candidate in ("/bin/bash", "/usr/bin/bash", "/bin/zsh", "/usr/bin/zsh", "/bin/sh"):
        if os.access(candidate, os.X_OK):
            return candidate

    return "/bin/sh"


def is_shell_command(obj: Any) -> bool:
    """Return True if *obj* walks like a ``ShellCommand``."""
    return (
        hasattr(obj, "status")
        and hasattr(obj, "result")
        and hasattr(obj, "kill")
        and hasattr(obj, "cleanup")
        and hasattr(obj, "background")
    )
