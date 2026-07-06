"""
Immediate command dispatch system.

Port of: src/utils/immediateCommand.ts (recovered from sourcemap).

Provides a priority-based command dispatch queue that can execute commands
immediately — bypassing the normal prompt loop — and return results inline.
Supports:
  - Priority-ordered dispatch with preemption for high-priority commands.
  - Synchronous and asynchronous command execution.
  - Command lifecycle hooks (pre-dispatch validation, post-dispatch reporting).
  - Timeout enforcement per command.
  - Integration with the existing ``Command`` / ``LocalCommand`` / ``PromptCommand``
    types from ``hare.app_types.command``.
  - Thread-safe enqueue and drain patterns (asyncio-based).
  - Built-in debugging and telemetry callbacks.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from hare.app_types.command import (
    Command,
    LocalCommand,
    LocalJSXCommand,
    PromptCommand,
)


# ---------------------------------------------------------------------------
# Priority levels for immediate dispatch
# ---------------------------------------------------------------------------


class ImmediateCommandPriority(IntEnum):
    """Priority levels — lower numeric value = higher urgency."""

    CRITICAL = 0   # System-critical (e.g. /exit, abort)
    HIGH = 10      # User-facing immediate (e.g. /clear, /compact)
    NORMAL = 50    # Default priority
    LOW = 100      # Background / best-effort
    IDLE = 200     # Only run when the queue is otherwise empty


# ---------------------------------------------------------------------------
# Dispatch status
# ---------------------------------------------------------------------------


class DispatchStatus(IntEnum):
    """Outcome of a dispatched command."""

    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    TIMED_OUT = 4
    REJECTED = 5   # pre-dispatch validation failed
    CANCELLED = 6  # cancelled by an external signal


# ---------------------------------------------------------------------------
# Immediate command envelope
# ---------------------------------------------------------------------------


@dataclass
class ImmediateCommandEnvelope:
    """Wraps a Command with dispatch metadata.

    Each enqueued command carries an id, priority, timeout, creation timestamp,
    and optional callbacks for lifecycle hooks and result reporting.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    command: Command = field(default_factory=lambda: LocalCommand(name="noop"))
    raw_line: str = ""               # The original slash-command line (e.g. "/clear --force")
    priority: ImmediateCommandPriority = ImmediateCommandPriority.NORMAL
    timeout_s: Optional[float] = None  # None = use global default
    created_at: float = field(default_factory=time.monotonic)

    # Lifecycle callbacks
    on_validate: Optional[Callable[["ImmediateCommandEnvelope"], bool]] = None
    on_before: Optional[Callable[["ImmediateCommandEnvelope"], None]] = None
    on_after: Optional[Callable[["ImmediateCommandEnvelope", "DispatchResult"], None]] = None
    on_error: Optional[Callable[["ImmediateCommandEnvelope", Exception], None]] = None

    # Context injected into the command's ``call(args, **context)``
    context: Dict[str, Any] = field(default_factory=dict)

    # Mutable fields managed by the dispatcher
    status: DispatchStatus = DispatchStatus.PENDING
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Exception] = None


# ---------------------------------------------------------------------------
# Dispatch result
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """Returned after a command finishes (or times out / is rejected)."""

    envelope_id: str
    command_name: str
    status: DispatchStatus
    data: Optional[Dict[str, Any]] = None
    error_message: str = ""
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Command validation: pre-dispatch checks
# ---------------------------------------------------------------------------


def _validate_envelope(env: ImmediateCommandEnvelope) -> bool:
    """Default pre-dispatch validation.

    Returns False to reject the command before it runs.
    Override per-command via ``on_validate`` on the envelope.
    """
    if env.command.name == "noop":
        return False
    if not isinstance(env.raw_line, str):
        return False
    return True


# ---------------------------------------------------------------------------
# Command-prefix stripping (mirrors _slash_payload in commands_impl/__init__.py)
# ---------------------------------------------------------------------------


def _strip_command_prefix(raw_line: str, command: Command) -> str:
    """Strip the leading ``/command_name`` (or alias) from *raw_line* so the
    command implementation receives only the argument payload.

    Matches the behavior of ``_slash_payload`` in ``commands_impl/__init__.py``.
    """
    line = raw_line.strip()
    if not line.startswith("/"):
        return line
    tokens = line.split(None, 1)
    head = tokens[0][1:]  # drop the leading "/"
    # Support nested /xxx/yyy form — take last segment
    head = head.rsplit("/", 1)[-1].lower()
    keys = {command.name.lower()}
    for alias in (command.aliases or []):
        keys.add(alias.lower())
    if head in keys:
        return tokens[1] if len(tokens) > 1 else ""
    return line


# ---------------------------------------------------------------------------
# Timeout enforcement coroutine
# ---------------------------------------------------------------------------


async def _run_with_timeout(
    coro: Awaitable[Any],
    timeout_s: float,
    env: ImmediateCommandEnvelope,
) -> Any:
    """Run a coroutine with a deadline.  Raises ``asyncio.TimeoutError`` on
    expiry and sets the envelope status accordingly."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        env.status = DispatchStatus.TIMED_OUT
        env.finished_at = time.monotonic()
        raise


# ---------------------------------------------------------------------------
# Core dispatcher
# ---------------------------------------------------------------------------


class ImmediateCommandDispatcher:
    """Priority-based immediate command dispatch queue.

    Commands are enqueued with an ``ImmediateCommandEnvelope`` and processed
    in priority order (lowest numeric value first).  The dispatcher supports
    concurrent drainers (bounded by ``max_concurrency``) and a global default
    command timeout.

    Usage::

        dispatcher = ImmediateCommandDispatcher(max_concurrency=4, default_timeout_s=30.0)
        dispatcher.start()

        env = ImmediateCommandEnvelope(
            command=some_local_cmd,
            raw_line="/clear --force",
            priority=ImmediateCommandPriority.HIGH,
        )
        await dispatcher.enqueue(env)

        result = await dispatcher.wait_for(env.id)
        # or drain all with: results = await dispatcher.drain_all()

        await dispatcher.stop()
    """

    def __init__(
        self,
        max_concurrency: int = 4,
        default_timeout_s: float = 30.0,
        *,
        on_dispatch_start: Optional[Callable[[ImmediateCommandEnvelope], None]] = None,
        on_dispatch_finish: Optional[Callable[[ImmediateCommandEnvelope, DispatchResult], None]] = None,
        on_queue_empty: Optional[Callable[[], None]] = None,
    ) -> None:
        self.max_concurrency = max_concurrency
        self.default_timeout_s = default_timeout_s

        # Observability callbacks
        self.on_dispatch_start = on_dispatch_start
        self.on_dispatch_finish = on_dispatch_finish
        self.on_queue_empty = on_queue_empty

        # Internal state
        self._queue: asyncio.PriorityQueue[tuple[int, int, ImmediateCommandEnvelope]] = (
            asyncio.PriorityQueue()
        )
        self._counter = 0                  # tie-break counter for stable ordering
        self._results: Dict[str, DispatchResult] = {}
        self._pending_futures: Dict[str, asyncio.Future[DispatchResult]] = {}
        self._running_tasks: set[asyncio.Task[Any]] = set()
        self._stop_event = asyncio.Event()
        self._drainer_task: Optional[asyncio.Task[Any]] = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the background drainer loop."""
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        self._drainer_task = asyncio.ensure_future(self._drain_loop())

    async def stop(self, *, cancel_running: bool = False) -> None:
        """Signal the drainer to stop and wait for in-flight commands.

        If *cancel_running* is True, any currently executing command tasks
        are cancelled.
        """
        if not self._started:
            return
        self._stop_event.set()
        if cancel_running:
            for t in list(self._running_tasks):
                t.cancel()
        if self._drainer_task is not None:
            try:
                await self._drainer_task
            except asyncio.CancelledError:
                pass
        self._started = False

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def enqueue(self, env: ImmediateCommandEnvelope) -> None:
        """Push a command envelope onto the priority queue.

        Raises ``ValueError`` if the dispatcher has been stopped.
        """
        if self._stop_event.is_set():
            raise ValueError("Cannot enqueue on a stopped dispatcher")
        self._counter += 1
        # PriorityQueue sorts by (priority, tie_break_counter, _) — lowest first
        await self._queue.put((int(env.priority), self._counter, env))

    async def enqueue_many(self, envelopes: List[ImmediateCommandEnvelope]) -> None:
        """Batch-enqueue multiple envelopes."""
        for env in envelopes:
            await self.enqueue(env)

    # ------------------------------------------------------------------
    # Wait for a specific envelope
    # ------------------------------------------------------------------

    async def wait_for(self, envelope_id: str) -> DispatchResult:
        """Block until the command identified by *envelope_id* finishes.

        Returns the ``DispatchResult``.  If the envelope id is unknown a
        result with status ``FAILED`` is returned immediately.
        """
        if envelope_id in self._results:
            return self._results[envelope_id]
        fut: asyncio.Future[DispatchResult] = asyncio.get_event_loop().create_future()
        self._pending_futures[envelope_id] = fut
        return await fut

    # ------------------------------------------------------------------
    # Drain all pending commands
    # ------------------------------------------------------------------

    async def drain_all(self) -> List[DispatchResult]:
        """Process every currently-enqueued command and return all results.

        This call does **not** stop the dispatcher — new commands can still be
        enqueued concurrently.
        """
        results: List[DispatchResult] = []
        while not self._queue.empty() or self._running_tasks:
            # Snapshot current queue depth
            snapshot: List[ImmediateCommandEnvelope] = []
            while not self._queue.empty():
                _, _, env = self._queue.get_nowait()
                snapshot.append(env)
            # Re-enqueue and wait for each
            futures = []
            for env in snapshot:
                await self._queue.put((int(env.priority), self._counter, env))
                self._counter += 1
                futures.append(self.wait_for(env.id))
            if futures:
                batch = await asyncio.gather(*futures, return_exceptions=True)
                for item in batch:
                    if isinstance(item, DispatchResult):
                        results.append(item)
        return results

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Approximate number of commands waiting in the queue."""
        return self._queue.qsize()

    @property
    def running_count(self) -> int:
        """Number of currently executing dispatch tasks."""
        return len(self._running_tasks)

    def result_for(self, envelope_id: str) -> Optional[DispatchResult]:
        """Return a finished result without waiting, or None."""
        return self._results.get(envelope_id)

    # ------------------------------------------------------------------
    # Internal drain loop
    # ------------------------------------------------------------------

    async def _drain_loop(self) -> None:
        while not self._stop_event.is_set():
            # Respect max concurrency
            while self.running_count >= self.max_concurrency:
                await asyncio.sleep(0.01)

            try:
                # Wait for the next envelope with a short poll so we can
                # notice stop_event
                prio, _counter, env = await asyncio.wait_for(
                    self._queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                if self.running_count == 0 and self._queue.empty():
                    if self.on_queue_empty:
                        self.on_queue_empty()
                continue

            task = asyncio.ensure_future(self._dispatch_one(env))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _dispatch_one(self, env: ImmediateCommandEnvelope) -> None:
        """Run a single envelope through validation -> execution -> result.

        This is the heart of the dispatcher.  It is intentionally linear so
        every step is observable and cancellable.
        """
        # ---- pre-dispatch ----
        if self.on_dispatch_start:
            try:
                self.on_dispatch_start(env)
            except Exception:
                pass  # observability callback must not crash the dispatcher

        if env.on_before:
            try:
                env.on_before(env)
            except Exception:
                pass

        # ---- validation ----
        validator = env.on_validate or _validate_envelope
        try:
            ok = validator(env)
        except Exception as exc:
            env.status = DispatchStatus.REJECTED
            env.error = exc
            result = DispatchResult(
                envelope_id=env.id,
                command_name=env.command.name,
                status=DispatchStatus.REJECTED,
                error_message=str(exc),
            )
            self._commit_result(env, result)
            return

        if not ok:
            env.status = DispatchStatus.REJECTED
            result = DispatchResult(
                envelope_id=env.id,
                command_name=env.command.name,
                status=DispatchStatus.REJECTED,
                error_message="Pre-dispatch validation returned False",
            )
            self._commit_result(env, result)
            return

        # ---- execution ----
        env.status = DispatchStatus.RUNNING
        env.started_at = time.monotonic()

        timeout = (
            env.timeout_s
            if env.timeout_s is not None
            else self.default_timeout_s
        )

        try:
            command_result = await self._execute_command(env, timeout)
        except asyncio.CancelledError:
            env.status = DispatchStatus.CANCELLED
            env.finished_at = time.monotonic()
            result = DispatchResult(
                envelope_id=env.id,
                command_name=env.command.name,
                status=DispatchStatus.CANCELLED,
                error_message="Command was cancelled",
            )
            self._commit_result(env, result)
            return
        except Exception as exc:
            env.status = DispatchStatus.FAILED
            env.error = exc
            env.finished_at = time.monotonic()
            if env.on_error:
                try:
                    env.on_error(env, exc)
                except Exception:
                    pass
            result = DispatchResult(
                envelope_id=env.id,
                command_name=env.command.name,
                status=DispatchStatus.FAILED,
                error_message=str(exc),
                duration_ms=(env.finished_at - env.started_at) * 1000,
            )
            self._commit_result(env, result)
            return

        # ---- success ----
        env.status = DispatchStatus.SUCCESS
        env.result = command_result
        env.finished_at = time.monotonic()

        duration_ms = (env.finished_at - env.started_at) * 1000
        result = DispatchResult(
            envelope_id=env.id,
            command_name=env.command.name,
            status=DispatchStatus.SUCCESS,
            data=command_result,
            duration_ms=duration_ms,
        )

        if env.on_after:
            try:
                env.on_after(env, result)
            except Exception:
                pass

        self._commit_result(env, result)

    async def _execute_command(
        self, env: ImmediateCommandEnvelope, timeout_s: float
    ) -> Dict[str, Any]:
        """Invoke the underlying command callable and return its result dict.

        Supports ``LocalCommand``, ``LocalJSXCommand`` (via ``.call(args, context)``)
        and ``PromptCommand`` (via ``.get_prompt_for_command(args, context)`` which
        returns a string — we wrap it).
        """
        cmd = env.command
        raw_line = env.raw_line
        # Strip leading /command_name so the implementation receives only args
        args_payload = _strip_command_prefix(raw_line, cmd)
        context = dict(env.context)

        # Dispatch by command type
        if isinstance(cmd, (LocalCommand, LocalJSXCommand)):
            call_fn = getattr(cmd, "call", None)
            if call_fn is None:
                raise RuntimeError(f"Command '{cmd.name}' has no callable")
            coro = _invoke_with_timeout(call_fn, args_payload, context, timeout_s)
            result = await coro
            if isinstance(result, dict):
                return result
            return {"type": "text", "text": str(result)}

        if isinstance(cmd, PromptCommand):
            get_prompt = getattr(cmd, "get_prompt_for_command", None)
            if get_prompt is None:
                raise RuntimeError(f"PromptCommand '{cmd.name}' has no get_prompt_for_command")
            prompt_text = await get_prompt(args_payload, context)
            return {"type": "prompt", "text": prompt_text}

        raise TypeError(f"Unsupported command type: {type(cmd).__name__}")

    def _commit_result(
        self, env: ImmediateCommandEnvelope, result: DispatchResult
    ) -> None:
        """Store the result and resolve any waiting future."""
        self._results[env.id] = result
        fut = self._pending_futures.pop(env.id, None)
        if fut is not None and not fut.done():
            fut.set_result(result)
        if self.on_dispatch_finish:
            try:
                self.on_dispatch_finish(env, result)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Timeout wrapper for arbitrary callables
# ---------------------------------------------------------------------------


async def _invoke_with_timeout(
    call_fn: Callable[..., Any],
    raw_line: str,
    context: Dict[str, Any],
    timeout_s: float,
) -> Any:
    """Call *call_fn* with a deadline, adapting its signature as needed.

    Inspects the function signature to avoid ``TypeError`` on mismatched
    parameter counts — similar to ``adapt_command_call`` in ``commands_impl/invoke.py``.
    """
    sig = inspect.signature(call_fn)
    params = list(sig.parameters.values())
    pos = [
        p
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]

    # Build the coroutine based on detected signature
    if len(pos) == 0:
        coro = call_fn()
    elif len(pos) == 1:
        n0 = pos[0].name
        if n0 in ("context", "ctx"):
            coro = call_fn(context)
        else:
            coro = call_fn(raw_line)
    else:
        # Two or more positional params -> (raw_line, context) by default
        try:
            coro = call_fn(raw_line, context)
        except TypeError:
            # fallback to kwargs
            coro = call_fn(raw_line, **context)

    if inspect.iscoroutine(coro):
        return await asyncio.wait_for(coro, timeout=timeout_s)
    # Sync function wrapped in a thread to avoid blocking
    if inspect.isawaitable(coro):
        return await asyncio.wait_for(coro, timeout=timeout_s)
    if callable(coro):
        # Edge case: the callable returned another callable
        raise RuntimeError("Command call returned a callable instead of a coroutine or value")

    return coro


# ---------------------------------------------------------------------------
# Convenience: build an envelope for a named command
# ---------------------------------------------------------------------------


async def build_envelope_for_command(
    command_name: str,
    raw_line: str,
    commands: List[Command],
    *,
    priority: ImmediateCommandPriority = ImmediateCommandPriority.NORMAL,
    timeout_s: Optional[float] = None,
    context: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> ImmediateCommandEnvelope:
    """Look up a command by name in *commands* and wrap it in an envelope.

    Returns ``None`` if no matching command is found.
    """
    from hare.commands import find_command as _find_command

    cmd = _find_command(command_name, commands)
    if cmd is None:
        raise KeyError(f"Command '{command_name}' not found in command list")

    return ImmediateCommandEnvelope(
        command=cmd,
        raw_line=raw_line,
        priority=priority,
        timeout_s=timeout_s,
        context=context or {},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Single-shot dispatch helper (stateless, no queue)
# ---------------------------------------------------------------------------


async def dispatch_immediate(
    command: Command,
    raw_line: str,
    *,
    context: Optional[Dict[str, Any]] = None,
    timeout_s: float = 30.0,
) -> DispatchResult:
    """Execute a single command immediately and return its result.

    This is a stateless convenience that does not use the dispatcher queue.
    Useful for one-off execution from the REPL or tool implementations.
    """
    env = ImmediateCommandEnvelope(
        command=command,
        raw_line=raw_line,
        priority=ImmediateCommandPriority.CRITICAL,
        timeout_s=timeout_s,
        context=context or {},
    )
    # Minimal inline dispatch — no queue, no concurrency limit
    dispatcher = ImmediateCommandDispatcher(max_concurrency=1, default_timeout_s=timeout_s)
    dispatcher.start()
    await dispatcher.enqueue(env)
    result = await dispatcher.wait_for(env.id)
    await dispatcher.stop()
    return result


# ---------------------------------------------------------------------------
# Filter / router helpers for command groups
# ---------------------------------------------------------------------------


def filter_envelopes_by_type(
    envelopes: List[ImmediateCommandEnvelope],
    command_type: type,
) -> List[ImmediateCommandEnvelope]:
    """Return envelopes whose command is an instance of *command_type*."""
    return [e for e in envelopes if isinstance(e.command, command_type)]


def group_envelopes_by_priority(
    envelopes: List[ImmediateCommandEnvelope],
) -> Dict[ImmediateCommandPriority, List[ImmediateCommandEnvelope]]:
    """Group envelopes into buckets keyed by priority."""
    groups: Dict[ImmediateCommandPriority, List[ImmediateCommandEnvelope]] = {}
    for env in envelopes:
        groups.setdefault(env.priority, []).append(env)
    return groups


def partition_results(
    results: List[DispatchResult],
) -> Dict[str, List[DispatchResult]]:
    """Partition results into 'success', 'failed', 'rejected', 'cancelled', 'timed_out'."""
    partitions: Dict[str, List[DispatchResult]] = {
        "success": [],
        "failed": [],
        "rejected": [],
        "cancelled": [],
        "timed_out": [],
    }
    for r in results:
        if r.status == DispatchStatus.SUCCESS:
            partitions["success"].append(r)
        elif r.status == DispatchStatus.FAILED:
            partitions["failed"].append(r)
        elif r.status == DispatchStatus.REJECTED:
            partitions["rejected"].append(r)
        elif r.status == DispatchStatus.CANCELLED:
            partitions["cancelled"].append(r)
        elif r.status == DispatchStatus.TIMED_OUT:
            partitions["timed_out"].append(r)
    return partitions


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


async def dispatch_with_retry(
    command: Command,
    raw_line: str,
    *,
    max_retries: int = 3,
    base_delay_s: float = 0.5,
    backoff_factor: float = 2.0,
    timeout_s: float = 30.0,
    context: Optional[Dict[str, Any]] = None,
    retry_on: Optional[Callable[[DispatchResult], bool]] = None,
) -> DispatchResult:
    """Dispatch a command with exponential backoff retry.

    Retries only when ``retry_on`` returns True (default: retry on FAILED or
    TIMED_OUT).
    """
    if retry_on is None:

        def _default_retry_on(r: DispatchResult) -> bool:
            return r.status in (DispatchStatus.FAILED, DispatchStatus.TIMED_OUT)

        retry_on = _default_retry_on

    last_result: Optional[DispatchResult] = None
    for attempt in range(max_retries + 1):
        last_result = await dispatch_immediate(
            command,
            raw_line,
            context=context,
            timeout_s=timeout_s,
        )
        if not retry_on(last_result):
            return last_result
        if attempt < max_retries:
            delay = base_delay_s * (backoff_factor ** attempt)
            await asyncio.sleep(delay)

    return last_result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Command cooldown / rate-limit tracker
# ---------------------------------------------------------------------------


class CommandCooldownTracker:
    """Prevents a command from being dispatched too frequently.

    Tracks last-dispatch timestamps per command name.  Callers check
    ``reject_if_cooldown()`` before dispatch and ``record()`` after success.

    All methods are safe for single-event-loop use (``time.monotonic``).
    """

    def __init__(self, default_cooldown_s: float = 5.0) -> None:
        self.default_cooldown_s = default_cooldown_s
        self._last: Dict[str, float] = {}

    def record(self, key: str) -> None:
        """Mark *key* as just-dispatched."""
        self._last[key] = time.monotonic()

    def reject_if_cooldown(
        self, key: str, cooldown_s: Optional[float] = None
    ) -> bool:
        """True when *key* is still within its cooldown window."""
        window = cooldown_s or self.default_cooldown_s
        ts = self._last.get(key)
        return ts is not None and (time.monotonic() - ts) < window

    def remaining_s(self, key: str, cooldown_s: Optional[float] = None) -> float:
        """Seconds left in the cooldown (0.0 if not cooling)."""
        window = cooldown_s or self.default_cooldown_s
        ts = self._last.get(key)
        if ts is None:
            return 0.0
        return max(0.0, window - (time.monotonic() - ts))

    def reset(self, key: Optional[str] = None) -> None:
        """Clear cooldown for *key*, or all keys when None."""
        if key is None:
            self._last.clear()
        else:
            self._last.pop(key, None)


# ---------------------------------------------------------------------------
# Preemption: interrupt lower-priority commands for urgent ones
# ---------------------------------------------------------------------------


async def preempt_lower_priority(
    dispatcher: ImmediateCommandDispatcher,
    incoming_priority: ImmediateCommandPriority,
    *,
    threshold: ImmediateCommandPriority = ImmediateCommandPriority.NORMAL,
) -> int:
    """Cancel running tasks whose priority is strictly lower (numerically
    greater) than *incoming_priority*, bounded by *threshold*.

    Returns the count of tasks cancelled.  Tasks must have been tagged with
    their envelope via ``_tag_task_with_envelope`` at dispatch time.

    Typical usage — call before enqueuing a CRITICAL or HIGH command::

        cancelled = await preempt_lower_priority(disp, env.priority)
        await disp.enqueue(env)
    """
    cancelled = 0
    inc = int(incoming_priority)
    limit = int(threshold)
    for task in list(dispatcher._running_tasks):
        if task.done():
            continue
        env_ref = getattr(task, "_immediate_env", None)
        if env_ref is None:
            continue
        task_prio = int(env_ref.priority)  # type: ignore[union-attr]
        if task_prio > inc and task_prio <= limit:
            task.cancel()
            env_ref.status = DispatchStatus.CANCELLED  # type: ignore[union-attr]
            cancelled += 1
    return cancelled


def _tag_task_with_envelope(
    task: asyncio.Task[Any], env: ImmediateCommandEnvelope
) -> None:
    """Attach the envelope to the task so ``preempt_lower_priority`` can
    inspect it later."""
    task._immediate_env = env  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Enqueue with integrated cooldown + preemption
# ---------------------------------------------------------------------------


async def enqueue_with_guard(
    dispatcher: ImmediateCommandDispatcher,
    env: ImmediateCommandEnvelope,
    *,
    cooldown: Optional[CommandCooldownTracker] = None,
    cooldown_key: Optional[str] = None,
    cooldown_s: Optional[float] = None,
    preempt: bool = True,
) -> DispatchResult:
    """Enqueue *env* after (optionally) checking cooldown and preempting
    lower-priority work.  Returns the ``DispatchResult``.

    When *cooldown* is provided and the key is still cooling, a REJECTED
    result is returned synchronously without enqueuing.

    When *preempt* is True and the envelope priority is HIGH or above,
    lower-priority running tasks are cancelled first.
    """
    # -- cooldown gate --
    if cooldown is not None and cooldown_key:
        if cooldown.reject_if_cooldown(cooldown_key, cooldown_s):
            return DispatchResult(
                envelope_id=env.id,
                command_name=env.command.name,
                status=DispatchStatus.REJECTED,
                error_message=(
                    f"Command '{env.command.name}' on cooldown "
                    f"({cooldown.remaining_s(cooldown_key, cooldown_s):.1f}s left)"
                ),
            )

    # -- preempt lower-priority tasks --
    if preempt and int(env.priority) <= int(ImmediateCommandPriority.HIGH):
        await preempt_lower_priority(dispatcher, env.priority)

    # -- inject task tagging so future preemptions see this task too --
    _orig = dispatcher._dispatch_one

    async def _tagged_dispatch(inner: ImmediateCommandEnvelope) -> None:
        task = asyncio.current_task()
        if task is not None:
            _tag_task_with_envelope(task, inner)
        await _orig(inner)

    dispatcher._dispatch_one = _tagged_dispatch  # type: ignore[assignment]
    try:
        await dispatcher.enqueue(env)
        if cooldown is not None and cooldown_key:
            cooldown.record(cooldown_key)
        return await dispatcher.wait_for(env.id)
    finally:
        dispatcher._dispatch_one = _orig  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Batch dispatch with aggregated result summary
# ---------------------------------------------------------------------------


@dataclass
class BatchDispatchSummary:
    """Aggregate outcome of a batch dispatch call."""

    total: int
    succeeded: int
    failed: int
    rejected: int
    cancelled: int
    timed_out: int
    total_duration_ms: float = 0.0
    results: List[DispatchResult] = field(default_factory=list)


async def dispatch_batch(
    envelopes: List[ImmediateCommandEnvelope],
    *,
    concurrency: int = 4,
    default_timeout_s: float = 30.0,
) -> BatchDispatchSummary:
    """Dispatch a batch of envelopes concurrently and return an aggregated
    summary once all complete.

    Internally creates a temporary dispatcher with the requested concurrency
    level, enqueues everything, drains, and tears down.
    """
    if not envelopes:
        return BatchDispatchSummary(total=0, succeeded=0, failed=0, rejected=0,
                                     cancelled=0, timed_out=0)

    dispatcher = ImmediateCommandDispatcher(
        max_concurrency=concurrency,
        default_timeout_s=default_timeout_s,
    )
    dispatcher.start()
    start = time.monotonic()

    try:
        await dispatcher.enqueue_many(envelopes)
        results = await dispatcher.drain_all()
    finally:
        await dispatcher.stop()

    duration = (time.monotonic() - start) * 1000
    return BatchDispatchSummary(
        total=len(results),
        succeeded=sum(1 for r in results if r.status == DispatchStatus.SUCCESS),
        failed=sum(1 for r in results if r.status == DispatchStatus.FAILED),
        rejected=sum(1 for r in results if r.status == DispatchStatus.REJECTED),
        cancelled=sum(1 for r in results if r.status == DispatchStatus.CANCELLED),
        timed_out=sum(1 for r in results if r.status == DispatchStatus.TIMED_OUT),
        total_duration_ms=duration,
        results=results,
    )


# ---------------------------------------------------------------------------
# Global singleton dispatcher
# ---------------------------------------------------------------------------

_global_dispatcher: Optional[ImmediateCommandDispatcher] = None
_global_dispatcher_lock = asyncio.Lock()


async def get_global_dispatcher(
    *,
    max_concurrency: int = 4,
    default_timeout_s: float = 30.0,
) -> ImmediateCommandDispatcher:
    """Return (and lazily start) a module-level singleton dispatcher.

    Safe for concurrent callers — initialization is guarded by an asyncio lock.
    The dispatcher is started automatically on first retrieval.
    """
    global _global_dispatcher
    if _global_dispatcher is not None and _global_dispatcher._started:
        return _global_dispatcher
    async with _global_dispatcher_lock:
        if _global_dispatcher is not None and _global_dispatcher._started:
            return _global_dispatcher
        _global_dispatcher = ImmediateCommandDispatcher(
            max_concurrency=max_concurrency,
            default_timeout_s=default_timeout_s,
        )
        _global_dispatcher.start()
        return _global_dispatcher


async def reset_global_dispatcher() -> None:
    """Stop and clear the singleton dispatcher so a fresh one can be created."""
    global _global_dispatcher
    async with _global_dispatcher_lock:
        if _global_dispatcher is not None:
            await _global_dispatcher.stop()
        _global_dispatcher = None


# ---------------------------------------------------------------------------
# Command argument tokenizer
# ---------------------------------------------------------------------------

@dataclass
class ParsedCommandArgs:
    """Structured representation of slash-command arguments.

    Slash-command lines like ``/foo --flag key=value positional1 positional2``
    are decomposed into:

    * *flags* — boolean switches (``--flag`` / ``--no-flag``)
    * *options* — key=value bindings
    * *positional* — bare tokens
    * *raw_args* — the string payload after the command name (convenience)
    """

    flags: List[str] = field(default_factory=list)
    options: Dict[str, str] = field(default_factory=dict)
    positional: List[str] = field(default_factory=list)
    raw_args: str = ""


def tokenize_command_args(
    raw_line: str,
    command_name: str = "",
) -> ParsedCommandArgs:
    """Parse a slash-command line into structured arguments.

    Strips the leading ``/command_name`` prefix, then tokenises the remaining
    payload into flags, key=value options, and positional arguments.

    Recognised forms::

        /command --flag       → flags=['flag']
        /command --no-flag    → flags=['no-flag']; semantic: flag=False
        /command key=val      → options={'key': 'val'}
        /command pos1 pos2    → positional=['pos1', 'pos2']
        /command --a --b=3 c  → flags=['a'], options={'b': '3'}, positional=['c']
    """
    line = raw_line.strip()
    if command_name:
        # Strip the leading /command_name (and any sub-command segments)
        if line.startswith("/"):
            tokens = line.split(None, 1)
            head = tokens[0][1:]  # drop leading "/"
            head = head.rsplit("/", 1)[-1].lower()
            keys = {command_name.lower()}
            if head in keys:
                payload = tokens[1] if len(tokens) > 1 else ""
            else:
                payload = line
        else:
            payload = line
    else:
        payload = line
    if payload.startswith("/"):
        tokens = payload.split(None, 1)
        payload = tokens[1] if len(tokens) > 1 else ""

    raw_args = payload
    if not payload:
        return ParsedCommandArgs(raw_args=raw_args)

    flags: List[str] = []
    options: Dict[str, str] = {}
    positional: List[str] = []

    for token in payload.split():
        # --key=value  →  options['key'] = 'value'
        if token.startswith("--") and "=" in token[2:]:
            key, _, val = token[2:].partition("=")
            options[key] = val
            continue
        # --key (bare flag, e.g. --force, --verbose, --no-cache)
        if token.startswith("--"):
            flags.append(token[2:])
            continue
        # key=value without leading -- (passthrough style)
        if "=" in token and not token.startswith("-"):
            key, _, val = token.partition("=")
            options[key] = val
            continue
        # Positional
        positional.append(token)

    return ParsedCommandArgs(
        flags=flags,
        options=options,
        positional=positional,
        raw_args=raw_args,
    )


# ---------------------------------------------------------------------------
# Dispatch result formatter (terminal display)
# ---------------------------------------------------------------------------

_STATUS_GLYPHS: Dict[DispatchStatus, str] = {
    DispatchStatus.SUCCESS: "✓",   # ✓
    DispatchStatus.FAILED: "✗",    # ✗
    DispatchStatus.REJECTED: "⚠",  # ⚠
    DispatchStatus.CANCELLED: "□", # □
    DispatchStatus.TIMED_OUT: "⏱", # ⏱
}


def format_dispatch_result(result: DispatchResult, *, verbose: bool = False) -> str:
    """Render a ``DispatchResult`` as a single human-readable line.

    Compact mode (default)::

        ✓ /clear  (12ms)
        ✗ /compact  (FAILED) timeout

    Verbose mode adds the error message and data payload.
    """
    glyph = _STATUS_GLYPHS.get(result.status, "?")
    name = f"/{result.command_name}" if not result.command_name.startswith("/") else result.command_name
    duration = f" ({result.duration_ms:.0f}ms)" if result.duration_ms else ""

    if result.status == DispatchStatus.SUCCESS:
        line = f"{glyph} {name}{duration}"
        if verbose and result.data:
            data_str = _truncate_repr(result.data, 120)
            line += f"  {data_str}"
        return line

    status_label = result.status.name
    line = f"{glyph} {name}{duration}  [{status_label}]"

    if result.error_message:
        line += f"  {result.error_message[:200]}"
    if verbose and result.data:
        data_str = _truncate_repr(result.data, 120)
        line += f"  data={data_str}"
    return line


def format_batch_summary(summary: BatchDispatchSummary) -> str:
    """Render a ``BatchDispatchSummary`` as a compact multi-line string."""
    parts = [f"Dispatched {summary.total} commands in {summary.total_duration_ms:.0f}ms"]
    if summary.succeeded:
        parts.append(f"  succeeded: {summary.succeeded}")
    if summary.failed:
        parts.append(f"  failed:    {summary.failed}")
    if summary.rejected:
        parts.append(f"  rejected:  {summary.rejected}")
    if summary.cancelled:
        parts.append(f"  cancelled: {summary.cancelled}")
    if summary.timed_out:
        parts.append(f"  timed_out: {summary.timed_out}")
    return "\n".join(parts)


def _truncate_repr(obj: Any, max_len: int) -> str:
    s = repr(obj)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Abort-aware dispatch
# ---------------------------------------------------------------------------


async def dispatch_with_abort_signal(
    command: Command,
    raw_line: str,
    abort_signal: Optional[Any] = None,
    *,
    context: Optional[Dict[str, Any]] = None,
    timeout_s: float = 30.0,
) -> DispatchResult:
    """Execute a command that can be cancelled via an external abort signal.

    If *abort_signal* is provided (compatible with ``AbortSignal`` or any object
    carrying an ``aborted`` boolean attribute), the dispatch task is cancelled
    when the signal is set.

    Returns a ``DispatchResult`` with status ``CANCELLED`` when aborted.
    """
    env = ImmediateCommandEnvelope(
        command=command,
        raw_line=raw_line,
        priority=ImmediateCommandPriority.HIGH,
        timeout_s=timeout_s,
        context=context or {},
    )
    dispatcher = ImmediateCommandDispatcher(max_concurrency=1, default_timeout_s=timeout_s)
    dispatcher.start()

    try:
        await dispatcher.enqueue(env)

        if abort_signal is not None:
            # Poll the abort signal in parallel with the dispatch
            async def _abort_poller() -> None:
                while not getattr(abort_signal, "aborted", False):
                    await asyncio.sleep(0.05)
                # Cancel the in-flight dispatch task
                for t in list(dispatcher._running_tasks):
                    t.cancel()

            poller_task = asyncio.ensure_future(_abort_poller())
            try:
                result = await dispatcher.wait_for(env.id)
            finally:
                poller_task.cancel()
                try:
                    await poller_task
                except asyncio.CancelledError:
                    pass
            return result
        else:
            return await dispatcher.wait_for(env.id)
    finally:
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# Dispatch telemetry / stats collector
# ---------------------------------------------------------------------------


@dataclass
class DispatchTelemetry:
    """Lightweight collector for dispatcher-level observability.

    Tracks aggregate counts and latencies.  All methods are safe for
    single-event-loop access (no locks needed).  Instantiate one per dispatcher
    or share globally.
    """

    total_enqueued: int = 0
    total_dispatched: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    total_rejected: int = 0
    total_cancelled: int = 0
    total_timed_out: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    errors_by_command: Dict[str, List[str]] = field(default_factory=dict)

    def record_enqueue(self) -> None:
        self.total_enqueued += 1

    def record_outcome(self, result: DispatchResult) -> None:
        """Digest a finished ``DispatchResult`` and update counters."""
        self.total_dispatched += 1
        self.total_latency_ms += result.duration_ms
        if result.duration_ms > self.max_latency_ms:
            self.max_latency_ms = result.duration_ms
        if result.duration_ms < self.min_latency_ms:
            self.min_latency_ms = result.duration_ms

        if result.status == DispatchStatus.SUCCESS:
            self.total_succeeded += 1
        elif result.status == DispatchStatus.FAILED:
            self.total_failed += 1
            if result.error_message:
                self.errors_by_command.setdefault(result.command_name, []).append(
                    result.error_message[:200]
                )
        elif result.status == DispatchStatus.REJECTED:
            self.total_rejected += 1
        elif result.status == DispatchStatus.CANCELLED:
            self.total_cancelled += 1
        elif result.status == DispatchStatus.TIMED_OUT:
            self.total_timed_out += 1

    @property
    def avg_latency_ms(self) -> float:
        if self.total_dispatched == 0:
            return 0.0
        return self.total_latency_ms / self.total_dispatched

    @property
    def success_rate(self) -> float:
        if self.total_dispatched == 0:
            return 0.0
        return self.total_succeeded / self.total_dispatched

    def snapshot(self) -> Dict[str, Any]:
        """Return a snapshot dict suitable for logging or debug output."""
        return {
            "enqueued": self.total_enqueued,
            "dispatched": self.total_dispatched,
            "succeeded": self.total_succeeded,
            "failed": self.total_failed,
            "rejected": self.total_rejected,
            "cancelled": self.total_cancelled,
            "timed_out": self.total_timed_out,
            "success_rate": f"{self.success_rate:.1%}",
            "avg_latency_ms": f"{self.avg_latency_ms:.1f}",
            "max_latency_ms": f"{self.max_latency_ms:.1f}",
            "min_latency_ms": f"{self.min_latency_ms:.1f}"
            if self.total_dispatched > 0
            else "n/a",
            "error_count_by_command": {
                k: len(v) for k, v in self.errors_by_command.items()
            },
        }

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.total_enqueued = 0
        self.total_dispatched = 0
        self.total_succeeded = 0
        self.total_failed = 0
        self.total_rejected = 0
        self.total_cancelled = 0
        self.total_timed_out = 0
        self.total_latency_ms = 0.0
        self.max_latency_ms = 0.0
        self.min_latency_ms = float("inf")
        self.errors_by_command.clear()


# ---------------------------------------------------------------------------
# Dispatch with integrated telemetry
# ---------------------------------------------------------------------------


async def dispatch_with_telemetry(
    command: Command,
    raw_line: str,
    telemetry: DispatchTelemetry,
    *,
    context: Optional[Dict[str, Any]] = None,
    timeout_s: float = 30.0,
) -> DispatchResult:
    """Dispatch a single command and feed the outcome into *telemetry*.

    Convenience wrapper around ``dispatch_immediate`` that records the result
    for observability.  Use this when you want per-command stats without
    introducing the full dispatcher queue.
    """
    telemetry.record_enqueue()
    result = await dispatch_immediate(
        command, raw_line, context=context, timeout_s=timeout_s
    )
    telemetry.record_outcome(result)
    return result
