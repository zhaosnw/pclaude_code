"""
SDK event queue for headless / streaming consumers.

Port of: src/utils/sdkEventQueue.ts
Expanded with: thread safety (RLock + Condition), event listeners,
filtered/batch drain, coalescing, overflow policies, queue stats,
convenience emitters, timestamp tracking, blocking consumer support,
per-task drain/query, selective removal, event validation, and
wire serialization.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Literal, TypedDict
from uuid import UUID, uuid4

from hare.bootstrap.state import get_is_non_interactive_session, get_session_id

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_QUEUE_SIZE = 1000
"""Default maximum number of events the queue can hold."""

MIN_MAX_QUEUE_SIZE = 2
"""Minimum allowed max queue size.  Values below this are clamped upward.
The :func:`set_max_queue_size` function already rejects non-positive values,
so this only prevents accidentally setting the queue to 1 (which is barely
functional -- a queue of size 1 is trivially full at all times)."""

MAX_MAX_QUEUE_SIZE = 100_000
"""Maximum allowed max queue size (prevents runaway memory usage)."""

# Well-known event subtypes
SUBTYPE_TASK_STARTED = "task_started"
SUBTYPE_TASK_PROGRESS = "task_progress"
SUBTYPE_TASK_NOTIFICATION = "task_notification"
SUBTYPE_SESSION_STATE_CHANGED = "session_state_changed"


# ---------------------------------------------------------------------------
# Overflow policy
# ---------------------------------------------------------------------------


class OverflowPolicy(Enum):
    """Queue overflow behavior when MAX_QUEUE_SIZE is reached."""

    DROP_OLDEST = auto()
    """Evict the oldest event (FIFO head) to make room -- current behaviour."""

    DROP_NEWEST = auto()
    """Silently discard the incoming event when the queue is full."""

    REJECT = auto()
    """Raise QueueFullError when the queue is full."""


class QueueFullError(RuntimeError):
    """Raised when overflow_policy is REJECT and the queue is full."""


# ---------------------------------------------------------------------------
# Event type definitions
# ---------------------------------------------------------------------------


class SdkWorkflowProgress(TypedDict, total=False):
    """Stub shape for workflow progress deltas (full schema in tools types)."""

    type: str
    index: int


class TaskStartedEvent(TypedDict, total=False):
    type: Literal["system"]
    subtype: Literal["task_started"]
    task_id: str
    tool_use_id: str
    description: str
    task_type: str
    workflow_name: str
    prompt: str


class TaskProgressUsage(TypedDict):
    total_tokens: int
    tool_uses: int
    duration_ms: int


class TaskProgressEvent(TypedDict, total=False):
    type: Literal["system"]
    subtype: Literal["task_progress"]
    task_id: str
    tool_use_id: str
    description: str
    usage: TaskProgressUsage
    last_tool_name: str
    summary: str
    workflow_progress: list[SdkWorkflowProgress]


class TaskNotificationSdkEvent(TypedDict, total=False):
    type: Literal["system"]
    subtype: Literal["task_notification"]
    task_id: str
    tool_use_id: str
    status: Literal["completed", "failed", "stopped"]
    output_file: str
    summary: str
    usage: TaskProgressUsage


class SessionStateChangedEvent(TypedDict):
    type: Literal["system"]
    subtype: Literal["session_state_changed"]
    state: Literal["idle", "running", "requires_action"]


SdkEvent = (
    TaskStartedEvent
    | TaskProgressEvent
    | TaskNotificationSdkEvent
    | SessionStateChangedEvent
)

SdkEventListener = Callable[[SdkEvent], None]
"""Callback invoked synchronously whenever an event is successfully enqueued.

Listeners receive the event *before* it is placed in the queue so they
can mutate it if needed (by returning a modified copy -- the event dict
itself should be treated as read-only by listeners).
"""

SdkEventPredicate = Callable[[SdkEvent], bool]
"""Predicate for filtering / selective removal of events from the queue."""

QueueFullCallback = Callable[[int, OverflowPolicy], None]
"""Callback invoked when the queue reaches capacity.

Args:
    current_size: The number of events currently in the queue.
    policy: The active overflow policy.
"""


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_queue: list[SdkEvent] = []
_listeners: list[SdkEventListener] = []
_lock: threading.RLock = threading.RLock()
_condition: threading.Condition = threading.Condition(lock=_lock)
_overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST
_max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE
_on_queue_full_callbacks: list[QueueFullCallback] = []
_stats: dict[str, int] = {
    "enqueued": 0,    # successfully added
    "drained": 0,     # removed via drain
    "dropped": 0,     # evicted due to overflow (DROP_OLDEST)
    "rejected": 0,    # discarded/rejected due to overflow (DROP_NEWEST / REJECT)
    "cleared": 0,     # removed via clear (not drain)
    "removed": 0,     # removed via remove_by_predicate
    "coalesced": 0,   # removed via coalesce
}
_history: list[SdkEvent] = []
"""Bounded history of recently drained events (for late-connecting consumers)."""
_max_history: int = 200
"""Maximum number of events retained in the history buffer."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_sdk_event(event: dict[str, Any]) -> None:
    """Validate that an event dict has the minimum required fields.

    Raises ValueError if the event is malformed.  This is called by
    enqueue_sdk_event and can also be used directly by callers that want
    early validation.

    Args:
        event: The event dict to validate.

    Raises:
        ValueError: If the event is missing required fields or has
            invalid values.
    """
    if not isinstance(event, dict):
        raise ValueError(f"SDK event must be a dict, got {type(event).__name__}")

    event_type = event.get("type")
    if event_type != "system":
        raise ValueError(f"SDK event 'type' must be 'system', got {event_type!r}")

    subtype = event.get("subtype")
    valid_subtypes = {
        SUBTYPE_TASK_STARTED,
        SUBTYPE_TASK_PROGRESS,
        SUBTYPE_TASK_NOTIFICATION,
        SUBTYPE_SESSION_STATE_CHANGED,
    }
    if subtype not in valid_subtypes:
        raise ValueError(
            f"SDK event 'subtype' must be one of {sorted(valid_subtypes)}, "
            f"got {subtype!r}"
        )

    # Per-subtype validation
    if subtype in (
        SUBTYPE_TASK_STARTED,
        SUBTYPE_TASK_PROGRESS,
        SUBTYPE_TASK_NOTIFICATION,
    ):
        task_id = event.get("task_id")
        if not task_id or not isinstance(task_id, str):
            raise ValueError(
                f"SDK event with subtype {subtype!r} requires a non-empty "
                f"string 'task_id'"
            )

    if subtype == SUBTYPE_TASK_NOTIFICATION:
        status = event.get("status")
        valid_statuses = {"completed", "failed", "stopped"}
        if status not in valid_statuses:
            raise ValueError(
                f"SDK task_notification 'status' must be one of "
                f"{sorted(valid_statuses)}, got {status!r}"
            )

    if subtype == SUBTYPE_SESSION_STATE_CHANGED:
        state = event.get("state")
        valid_states = {"idle", "running", "requires_action"}
        if state not in valid_states:
            raise ValueError(
                f"SDK session_state_changed 'state' must be one of "
                f"{sorted(valid_states)}, got {state!r}"
            )

    if subtype == SUBTYPE_TASK_PROGRESS:
        usage = event.get("usage")
        if not isinstance(usage, dict):
            raise ValueError(
                "SDK task_progress 'usage' must be a dict with "
                "'total_tokens', 'tool_uses', 'duration_ms'"
            )


# ---------------------------------------------------------------------------
# Max queue size configuration
# ---------------------------------------------------------------------------


def set_max_queue_size(size: int) -> None:
    """Set the maximum number of events the queue can hold.

    The queue is resized immediately: if the new size is smaller than the
    current queue length, oldest events are evicted according to the
    current overflow policy.

    Args:
        size: New max queue size (clamped to
            [MIN_MAX_QUEUE_SIZE, MAX_MAX_QUEUE_SIZE]).

    Raises:
        ValueError: If size is non-positive.
    """
    global _max_queue_size

    if size < 1:
        raise ValueError(f"Max queue size must be positive, got {size}")

    clamped = max(MIN_MAX_QUEUE_SIZE, min(size, MAX_MAX_QUEUE_SIZE))

    with _lock:
        _max_queue_size = clamped
        # If the queue exceeds the new max, trim it
        while len(_queue) > _max_queue_size:
            if _overflow_policy in (OverflowPolicy.DROP_OLDEST, OverflowPolicy.REJECT):
                _queue.pop(0)
                _stats["dropped"] += 1
            else:  # DROP_NEWEST
                _queue.pop()
                _stats["dropped"] += 1


def get_max_queue_size() -> int:
    """Return the current maximum queue size."""
    with _lock:
        return _max_queue_size


def is_queue_full() -> bool:
    """Return True if the queue is at or above capacity."""
    with _lock:
        return len(_queue) >= _max_queue_size


def is_queue_empty() -> bool:
    """Return True if the queue contains no events."""
    with _lock:
        return len(_queue) == 0


def capacity_remaining() -> int:
    """Return the number of additional events the queue can accept."""
    with _lock:
        return max(0, _max_queue_size - len(_queue))


# ---------------------------------------------------------------------------
# Overflow policy configuration
# ---------------------------------------------------------------------------


def set_overflow_policy(policy: OverflowPolicy) -> None:
    """Set the overflow policy for the global queue.

    Args:
        policy: One of DROP_OLDEST, DROP_NEWEST, or REJECT.
    """
    global _overflow_policy
    with _lock:
        _overflow_policy = policy


def get_overflow_policy() -> OverflowPolicy:
    """Return the current overflow policy."""
    with _lock:
        return _overflow_policy


# ---------------------------------------------------------------------------
# Queue-full callback management
# ---------------------------------------------------------------------------


def add_on_queue_full_callback(cb: QueueFullCallback) -> None:
    """Register a callback invoked when the queue reaches capacity.

    The callback receives the current queue size and active overflow
    policy.  It is called synchronously under the lock *before* any
    overflow action (drop/reject) takes place.

    Args:
        cb: Callable receiving (size: int, policy: OverflowPolicy).
    """
    with _lock:
        if cb not in _on_queue_full_callbacks:
            _on_queue_full_callbacks.append(cb)


def remove_on_queue_full_callback(cb: QueueFullCallback) -> None:
    """Unregister a previously added queue-full callback.

    Args:
        cb: The callable to remove.
    """
    with _lock:
        if cb in _on_queue_full_callbacks:
            _on_queue_full_callbacks.remove(cb)


def _notify_queue_full(size: int, policy: OverflowPolicy) -> None:
    """Call all registered queue-full callbacks (lock already held)."""
    for cb in _on_queue_full_callbacks:
        try:
            cb(size, policy)
        except Exception:
            # Queue-full callbacks must not prevent overflow handling.
            pass


# ---------------------------------------------------------------------------
# Listener management
# ---------------------------------------------------------------------------


def add_sdk_event_listener(listener: SdkEventListener) -> None:
    """Register a callback invoked when an event is enqueued.

    Listeners are called synchronously (under lock) before the event is
    placed in the queue.  If a listener raises, the exception propagates
    and the event is *not* enqueued.

    Because the lock is reentrant (RLock), listeners may safely call
    enqueue_sdk_event or other queue operations without deadlocking.

    Args:
        listener: Callable receiving the event dict.
    """
    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)


def remove_sdk_event_listener(listener: SdkEventListener) -> None:
    """Unregister a previously added listener.

    Args:
        listener: The callable to remove.
    """
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def _notify_listeners(event: SdkEvent) -> None:
    """Call all registered listeners with the given event (lock already held)."""
    for cb in _listeners:
        cb(event)


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


def enqueue_sdk_event(event: SdkEvent, *, validate: bool = True) -> None:
    """Queue an SDK event (non-interactive sessions only).

    In interactive (TUI) sessions events are silently dropped because the
    queue is never drained by a streaming consumer.

    Overflow behaviour is governed by :func:`set_overflow_policy`:

    * DROP_OLDEST (default) -- evicts the oldest event to make room.
    * DROP_NEWEST -- silently discards the incoming event.
    * REJECT -- raises :class:`QueueFullError`.

    Args:
        event: The SDK event to enqueue.
        validate: If True (default), validate the event structure before
            enqueuing.  Set to False only for performance-critical paths
            where the caller guarantees correctness.

    Raises:
        ValueError: If ``validate`` is True and the event is malformed.
        QueueFullError: When overflow_policy is REJECT and the queue is full.
    """
    if not get_is_non_interactive_session():
        return

    if validate:
        validate_sdk_event(event)  # type: ignore[arg-type]

    with _lock:
        policy = _overflow_policy
        qlen = len(_queue)
        max_size = _max_queue_size

        if qlen >= max_size:
            _notify_queue_full(qlen, policy)

            if policy is OverflowPolicy.DROP_OLDEST:
                _queue.pop(0)
                _stats["dropped"] += 1
            elif policy is OverflowPolicy.DROP_NEWEST:
                _stats["rejected"] += 1
                return
            elif policy is OverflowPolicy.REJECT:
                _stats["rejected"] += 1
                raise QueueFullError(
                    f"SDK event queue is full ({max_size} events). "
                    f"Consider draining or increasing max queue size."
                )

        _notify_listeners(event)
        _queue.append(event)
        _stats["enqueued"] += 1
        # Wake any threads blocked in wait_for_events()
        _condition.notify_all()


# ---------------------------------------------------------------------------
# Blocking consumer support
# ---------------------------------------------------------------------------


def wait_for_events(timeout: float | None = None) -> bool:
    """Block until at least one event is enqueued or the timeout expires.

    Designed for streaming consumers that want to avoid busy-waiting
    between drain calls.  Use in a loop:

        while True:
            if wait_for_events(timeout=5.0):
                events = drain_sdk_events()
                process(events)

    Args:
        timeout: Maximum time to wait in seconds.  None means wait
            indefinitely.

    Returns:
        True if events are available (queue is non-empty), False if the
        wait timed out.
    """
    with _condition:
        if len(_queue) > 0:
            return True
        _condition.wait(timeout=timeout)
        return len(_queue) > 0


def wait_for_events_or_predicate(
    predicate: SdkEventPredicate,
    timeout: float | None = None,
) -> bool:
    """Block until an event matching *predicate* is enqueued or timeout.

    Unlike wait_for_events (which returns true for any event), this
    waits until at least one event in the queue satisfies the predicate.

    Args:
        predicate: A callable that receives an event and returns True if
            it matches the desired condition.
        timeout: Maximum time to wait in seconds.  None means wait
            indefinitely.

    Returns:
        True if at least one matching event is in the queue, False if
        the wait timed out.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    with _condition:
        while True:
            if any(predicate(e) for e in _queue):
                return True
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
            _condition.wait(timeout=remaining)


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------


@dataclass
class DrainedSdkEvent:
    """Event with session metadata and timestamp after drain."""

    event: dict[str, Any]
    uuid: UUID
    session_id: str
    timestamp_epoch_ms: float = 0.0


def _annotate_events(events: list[SdkEvent]) -> list[dict[str, Any]]:
    """Annotate events with uuid, session_id, timestamp (outside the lock)."""
    sid = get_session_id()
    now_ms = time.time() * 1000
    out: list[dict[str, Any]] = []
    for e in events:
        row = dict(copy.deepcopy(e))
        row["uuid"] = str(uuid4())
        row["session_id"] = sid
        row["timestamp_epoch_ms"] = now_ms
        out.append(row)
    return out


def _annotate_events_structured(events: list[SdkEvent]) -> list[DrainedSdkEvent]:
    """Annotate events as typed DrainedSdkEvent objects (outside the lock)."""
    sid = get_session_id()
    now_ms = time.time() * 1000
    out: list[DrainedSdkEvent] = []
    for e in events:
        out.append(
            DrainedSdkEvent(
                event=dict(copy.deepcopy(e)),
                uuid=uuid4(),
                session_id=sid,
                timestamp_epoch_ms=now_ms,
            )
        )
    return out


def _record_history(events: list[SdkEvent]) -> None:
    """Append a snapshot of drained events to the history buffer."""
    global _history
    for e in events:
        _history.append(dict(copy.deepcopy(e)))
    # Trim history to max size
    while len(_history) > _max_history:
        _history.pop(0)


def drain_sdk_events() -> list[dict[str, Any]]:
    """Drain all queued events, annotating each with uuid and session_id.

    Returns:
        List of event dicts with ``uuid`` and ``session_id`` keys added.
        Returns an empty list when the queue is empty.
    """
    with _lock:
        if not _queue:
            return []
        events = _queue[:]
        _queue.clear()
        _stats["drained"] += len(events)
        _record_history(events)

    return _annotate_events(events)


def drain_sdk_events_structured() -> list[DrainedSdkEvent]:
    """Drain all queued events as typed :class:`DrainedSdkEvent` objects.

    Returns:
        List of :class:`DrainedSdkEvent` instances, or empty list when the
        queue is empty.
    """
    with _lock:
        if not _queue:
            return []
        events = _queue[:]
        _queue.clear()
        _stats["drained"] += len(events)
        _record_history(events)

    return _annotate_events_structured(events)


def drain_sdk_events_filtered(
    subtype: str | None = None,
    task_id: str | None = None,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Drain events matching optional filters.

    Args:
        subtype: If given, only drain events whose ``subtype`` matches
            (e.g. ``"task_started"``, ``"task_progress"``,
            ``"task_notification"``, ``"session_state_changed"``).
        task_id: If given, only drain events whose ``task_id`` matches.
        max_count: If given, drain at most this many events.

    Returns:
        List of matching event dicts annotated with uuid/session_id/timestamp.
    """
    with _lock:
        if not _queue:
            return []

        matched: list[SdkEvent] = []
        remaining: list[SdkEvent] = []
        limit = max_count if max_count is not None else len(_queue)

        for event in _queue:
            if len(matched) >= limit:
                remaining.append(event)
                continue

            if subtype is not None and event.get("subtype") != subtype:
                remaining.append(event)
                continue

            if task_id is not None and event.get("task_id") != task_id:
                remaining.append(event)
                continue

            matched.append(event)

        drained_count = len(matched)
        _queue[:] = remaining
        _stats["drained"] += drained_count
        _record_history(matched)

    return _annotate_events(matched)


def drain_sdk_events_batch(max_count: int) -> list[dict[str, Any]]:
    """Drain at most ``max_count`` events from the front of the queue.

    Args:
        max_count: Maximum number of events to drain.  Must be >= 0.

    Returns:
        List of drained event dicts, up to ``max_count``.

    Raises:
        ValueError: If ``max_count`` is negative.
    """
    if max_count < 0:
        raise ValueError(f"max_count must be >= 0, got {max_count}")

    with _lock:
        if not _queue:
            return []
        count = min(max_count, len(_queue))
        if count == 0:
            return []
        events = _queue[:count]
        del _queue[:count]
        _stats["drained"] += count
        _record_history(events)

    return _annotate_events(events)


def drain_sdk_events_for_task(task_id: str) -> list[dict[str, Any]]:
    """Drain all events belonging to a specific task.

    This is a convenience wrapper around :func:`drain_sdk_events_filtered`
    that drains *all* subtypes (task_started, task_progress,
    task_notification) for the given task_id.

    Args:
        task_id: The task identifier.

    Returns:
        List of matching event dicts annotated with uuid/session_id/timestamp.
    """
    return drain_sdk_events_filtered(task_id=task_id)


def drain_sdk_events_by_subtype(subtype: str) -> list[dict[str, Any]]:
    """Drain all events of a specific subtype.

    This is a convenience wrapper around :func:`drain_sdk_events_filtered`
    that drains all events matching the given subtype.

    Args:
        subtype: The event subtype to match (e.g. ``"task_progress"``).

    Returns:
        List of matching event dicts annotated with uuid/session_id/timestamp.
    """
    return drain_sdk_events_filtered(subtype=subtype)


def drain_latest_progress_per_task() -> list[dict[str, Any]]:
    """Drain only the latest task_progress event per task.

    For each task_id that has multiple progress events in the queue,
    only the most recent one is drained.  All non-progress events and
    the single latest progress per task are returned.

    This is useful for consumers that want a compact summary rather than
    every incremental progress update.

    Returns:
        List of event dicts annotated with uuid/session_id/timestamp.
        Returns an empty list when the queue is empty.
    """
    with _lock:
        if not _queue:
            return []

        # Single pass: find latest progress per task, keep non-progress events
        non_progress: list[SdkEvent] = []
        latest_progress: OrderedDict[str, SdkEvent] = OrderedDict()
        skipped_progress_count = 0

        for event in _queue:
            if event.get("subtype") == SUBTYPE_TASK_PROGRESS:
                tid = event.get("task_id", "__unknown__")
                # OrderedDict remembers insertion order, so the last
                # write per key is what we keep.
                if tid in latest_progress:
                    skipped_progress_count += 1
                latest_progress[tid] = event
                # Move-to-end so the key appears in the order of the
                # *last* occurrence in the original queue.
                latest_progress.move_to_end(tid)
            else:
                non_progress.append(event)

        # Rebuild the queue: only non-progress events (progress events are
        # all drained -- either retained as "latest" or discarded).
        _queue[:] = non_progress
        matched = list(latest_progress.values())
        total_drained = len(matched) + skipped_progress_count
        _stats["drained"] += len(matched)
        # Track skipped (coalesced-away) progress events separately
        if skipped_progress_count > 0:
            _stats["coalesced"] += skipped_progress_count
        _record_history(matched)

    return _annotate_events(matched)


def drain_sdk_events_until(
    predicate: SdkEventPredicate,
    *,
    max_events: int | None = None,
    inclusive: bool = True,
) -> list[dict[str, Any]]:
    """Drain events from the front until *predicate* returns True.

    Args:
        predicate: A callable that receives each event and returns True
            when the stopping condition is met.
        max_events: If given, drain at most this many events regardless
            of whether the predicate was satisfied.
        inclusive: If True (default), the event that satisfied the
            predicate is included in the drain.  If False, it is left
            in the queue.

    Returns:
        List of drained event dicts annotated with uuid/session_id/timestamp.
    """
    with _lock:
        if not _queue:
            return []

        drained: list[SdkEvent] = []
        limit = max_events if max_events is not None else len(_queue)
        stop_index: int | None = None

        for i, event in enumerate(_queue):
            if len(drained) >= limit:
                break
            if predicate(event):
                if inclusive:
                    drained.append(event)
                stop_index = i + (0 if inclusive else 1)
                break
            drained.append(event)

        if stop_index is not None:
            del _queue[:stop_index]
        elif drained:
            del _queue[: len(drained)]

        count = len(drained)
        _stats["drained"] += count
        _record_history(drained)

    return _annotate_events(drained)


# ---------------------------------------------------------------------------
# Remove by predicate (without draining)
# ---------------------------------------------------------------------------


def remove_events_by_predicate(predicate: SdkEventPredicate) -> int:
    """Remove events matching *predicate* from the queue without draining.

    Removed events are NOT annotated with uuid/session_id and are NOT
    returned to the caller.  Use this for cleanup, not for consumption.

    Args:
        predicate: A callable that receives each event and returns True
            if it should be removed.

    Returns:
        The number of events removed.
    """
    with _lock:
        before = len(_queue)
        _queue[:] = [e for e in _queue if not predicate(e)]
        removed = before - len(_queue)
        _stats["removed"] += removed
        return removed


# ---------------------------------------------------------------------------
# Coalesce (deduplicate)
# ---------------------------------------------------------------------------


def coalesce_sdk_events() -> int:
    """Deduplicate consecutive task_progress events for the same task_id.

    For each task_id, only the *latest* task_progress event is kept.
    Older progress events for the same task are removed.  Non-progress
    events and progress events for distinct tasks are left in place.

    This is an in-place deduplication: events are removed from the queue
    without being drained or annotated.

    Returns:
        The number of events removed.
    """
    with _lock:
        if not _queue:
            return 0

        # Track the latest progress position per task
        per_task_index: dict[str, int] = {}
        progress_indices: dict[str, list[int]] = defaultdict(list)

        for i, event in enumerate(_queue):
            if event.get("subtype") == SUBTYPE_TASK_PROGRESS:
                tid = event.get("task_id", "__unknown__")
                per_task_index[tid] = i
                progress_indices[tid].append(i)

        # Build set of indices to remove (all progress events except the latest per task)
        to_remove: set[int] = set()
        for tid, indices in progress_indices.items():
            latest = per_task_index[tid]
            for idx in indices:
                if idx != latest:
                    to_remove.add(idx)

        if not to_remove:
            return 0

        _queue[:] = [e for i, e in enumerate(_queue) if i not in to_remove]
        removed = len(to_remove)
        _stats["coalesced"] += removed
        return removed


# ---------------------------------------------------------------------------
# Peek / inspect (without draining)
# ---------------------------------------------------------------------------


def peek_sdk_events(max_count: int | None = None) -> list[dict[str, Any]]:
    """Return a snapshot of queued events without draining them.

    Args:
        max_count: If given, return at most this many events (from front).

    Returns:
        Shallow-copy list of event dicts (without uuid/session_id).
    """
    with _lock:
        if not _queue:
            return []
        if max_count is None:
            return [dict(copy.deepcopy(e)) for e in _queue]
        return [dict(copy.deepcopy(e)) for e in _queue[:max_count]]


def get_queue_size() -> int:
    """Return the current number of events in the queue."""
    with _lock:
        return len(_queue)


def has_events_for_task(task_id: str) -> bool:
    """Return True if the queue contains any events for the given task_id.

    Args:
        task_id: The task identifier to check for.

    Returns:
        True if at least one event has a matching ``task_id``.
    """
    with _lock:
        return any(e.get("task_id") == task_id for e in _queue)


def count_events_for_task(task_id: str) -> int:
    """Return the number of events in the queue for the given task_id.

    Args:
        task_id: The task identifier to count events for.

    Returns:
        Count of events whose ``task_id`` matches.
    """
    with _lock:
        return sum(1 for e in _queue if e.get("task_id") == task_id)


def get_latest_event_for_task(task_id: str) -> dict[str, Any] | None:
    """Return the most recently enqueued event for a task (without draining).

    Args:
        task_id: The task identifier.

    Returns:
        A deep-copy of the latest event dict for the task, or None if no
        events exist for the given task.
    """
    with _lock:
        # Scan from the end (most recent) to find first match
        for i in range(len(_queue) - 1, -1, -1):
            if _queue[i].get("task_id") == task_id:
                return dict(copy.deepcopy(_queue[i]))
        return None


def event_subtype(event: SdkEvent) -> str:
    """Extract the subtype string from an event dict.

    Args:
        event: The event dict.

    Returns:
        The ``subtype`` value, or ``"unknown"`` if not present.
    """
    return str(event.get("subtype", "unknown"))


# ---------------------------------------------------------------------------
# History (recently drained events)
# ---------------------------------------------------------------------------


def get_recent_events(
    max_count: int | None = None,
    *,
    subtype: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return recently drained events from the history buffer.

    The history buffer retains up to ``_max_history`` events that were
    previously drained.  This allows late-connecting consumers to catch
    up on events they missed.

    Args:
        max_count: If given, return at most this many events (most recent).
        subtype: If given, filter to events of this subtype.
        task_id: If given, filter to events for this task.

    Returns:
        List of deep-copied event dicts (most recent first).
    """
    with _lock:
        events = list(_history)

    # Apply filters (newest-first iteration)
    result: list[dict[str, Any]] = []
    for e in reversed(events):
        if subtype is not None and e.get("subtype") != subtype:
            continue
        if task_id is not None and e.get("task_id") != task_id:
            continue
        result.append(dict(copy.deepcopy(e)))
        if max_count is not None and len(result) >= max_count:
            break

    return result


def clear_history() -> None:
    """Clear the history buffer of recently drained events."""
    global _history
    with _lock:
        _history.clear()


# ---------------------------------------------------------------------------
# Clear (without draining)
# ---------------------------------------------------------------------------


def clear_sdk_events() -> int:
    """Discard all queued events without draining them.

    Returns:
        The number of events that were discarded.
    """
    with _lock:
        count = len(_queue)
        _queue.clear()
        _stats["cleared"] += count
        return count


# ---------------------------------------------------------------------------
# Wire serialization
# ---------------------------------------------------------------------------


def serialize_events_for_wire(events: list[dict[str, Any]]) -> str:
    """JSON-serialize a list of event dicts for transport over the wire.

    Handles UUID, datetime, and other non-JSON-serializable types by
    converting them to strings.  This is suitable for bridge transport
    (SSE, WebSocket, HTTP response).

    Args:
        events: List of event dicts (typically the output of
            :func:`drain_sdk_events` or related functions).

    Returns:
        A JSON string suitable for wire transport.
    """
    return json.dumps(events, default=_json_serializer, separators=(",", ":"))


def _json_serializer(obj: Any) -> Any:
    """Fallback JSON serializer for non-standard types."""
    if isinstance(obj, UUID):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def serialize_single_event_for_wire(event: dict[str, Any]) -> str:
    """JSON-serialize a single event dict for wire transport.

    Args:
        event: A single event dict.

    Returns:
        A JSON string.
    """
    return json.dumps(event, default=_json_serializer, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def get_queue_stats() -> dict[str, int]:
    """Return queue operation counters.

    Keys: ``enqueued``, ``drained``, ``dropped``, ``rejected``, ``cleared``,
    ``removed``, ``coalesced``.
    """
    with _lock:
        return dict(_stats)


def reset_queue_stats() -> None:
    """Reset all queue operation counters to zero."""
    global _stats
    with _lock:
        _stats = {
            "enqueued": 0,
            "drained": 0,
            "dropped": 0,
            "rejected": 0,
            "cleared": 0,
            "removed": 0,
            "coalesced": 0,
        }


def get_queue_breakdown() -> dict[str, int]:
    """Return count of events currently in the queue grouped by subtype.

    Returns:
        Dict mapping subtype string to event count, e.g.
        ``{"task_started": 2, "task_progress": 5, "task_notification": 1}``.
    """
    with _lock:
        counts: dict[str, int] = defaultdict(int)
        for event in _queue:
            subtype = event.get("subtype", "unknown")
            counts[str(subtype)] += 1
        return dict(counts)


def get_queue_info() -> dict[str, Any]:
    """Return comprehensive queue state information.

    Useful for debugging, monitoring, and health checks.  Includes
    current size, max size, overflow policy, breakdown by subtype,
    capacity remaining, history size, listener count, and stats.

    Returns:
        A dict with queue metadata and current state.
    """
    with _lock:
        breakdown: dict[str, int] = defaultdict(int)
        per_task: dict[str, int] = defaultdict(int)
        for event in _queue:
            subtype = event.get("subtype", "unknown")
            breakdown[str(subtype)] += 1
            tid = event.get("task_id")
            if tid:
                per_task[str(tid)] += 1

        return {
            "size": len(_queue),
            "max_size": _max_queue_size,
            "capacity_remaining": max(0, _max_queue_size - len(_queue)),
            "overflow_policy": _overflow_policy.name.lower(),
            "breakdown_by_subtype": dict(breakdown),
            "breakdown_by_task": dict(per_task),
            "history_size": len(_history),
            "listener_count": len(_listeners),
            "stats": dict(_stats),
        }


# ---------------------------------------------------------------------------
# Convenience: TaskProgressUsage factory
# ---------------------------------------------------------------------------


def create_task_usage(
    total_tokens: int = 0,
    tool_uses: int = 0,
    duration_ms: int = 0,
) -> TaskProgressUsage:
    """Create a properly-typed TaskProgressUsage dict.

    Args:
        total_tokens: Cumulative token count.
        tool_uses: Cumulative tool use count.
        duration_ms: Elapsed duration in milliseconds.

    Returns:
        A dict with ``total_tokens``, ``tool_uses``, and ``duration_ms``
        keys suitable for use with :func:`emit_task_progress_sdk`.
    """
    return TaskProgressUsage(
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Convenience emitters
# ---------------------------------------------------------------------------


def emit_task_started_sdk(
    task_id: str,
    description: str,
    *,
    tool_use_id: str | None = None,
    task_type: str | None = None,
    workflow_name: str | None = None,
    prompt: str | None = None,
) -> None:
    """Emit task_started when a new background task is registered.

    This is the opening bookend for task lifecycle events; pair it with
    :func:`emit_task_terminated_sdk` (or task_notification via
    :func:`emit_task_notification_sdk`) to close the lifecycle.

    Args:
        task_id: Unique task identifier.
        description: Human-readable task description.
        tool_use_id: The tool_use_id of the Task tool invocation (if known).
        task_type: Task type string (e.g. ``"shell"``, ``"agent"``, etc.).
        workflow_name: Name of the workflow phase, if part of a workflow.
        prompt: The original prompt the task was given.
    """
    event: TaskStartedEvent = {
        "type": "system",
        "subtype": "task_started",
        "task_id": task_id,
        "description": description,
    }
    if tool_use_id:
        event["tool_use_id"] = tool_use_id
    if task_type:
        event["task_type"] = task_type
    if workflow_name:
        event["workflow_name"] = workflow_name
    if prompt:
        event["prompt"] = prompt
    enqueue_sdk_event(event)


def emit_task_progress_sdk(
    task_id: str,
    description: str,
    usage: TaskProgressUsage,
    *,
    tool_use_id: str | None = None,
    last_tool_name: str | None = None,
    summary: str | None = None,
    workflow_progress: list[SdkWorkflowProgress] | None = None,
) -> None:
    """Emit task_progress with usage metrics for an in-flight task.

    Args:
        task_id: Task identifier.
        description: Current status description.
        usage: Usage metrics dict with ``total_tokens``, ``tool_uses``,
            ``duration_ms``.
        tool_use_id: The tool_use_id of the Task tool invocation.
        last_tool_name: Name of the most recent tool used by the task.
        summary: Human-readable summary of progress so far.
        workflow_progress: Delta batch of workflow state changes for
            phase-progress display.
    """
    event: TaskProgressEvent = {
        "type": "system",
        "subtype": "task_progress",
        "task_id": task_id,
        "description": description,
        "usage": usage,
    }
    if tool_use_id:
        event["tool_use_id"] = tool_use_id
    if last_tool_name:
        event["last_tool_name"] = last_tool_name
    if summary:
        event["summary"] = summary
    if workflow_progress:
        event["workflow_progress"] = workflow_progress
    enqueue_sdk_event(event)


def emit_task_notification_sdk(
    task_id: str,
    status: Literal["completed", "failed", "stopped"],
    *,
    tool_use_id: str | None = None,
    summary: str | None = None,
    output_file: str | None = None,
    usage: TaskProgressUsage | None = None,
) -> None:
    """Emit task_notification when a task reaches a terminal state.

    This is the closing bookend for task lifecycle events.  Use
    :func:`emit_task_terminated_sdk` for the same purpose (legacy alias
    kept for backwards compatibility).

    Args:
        task_id: Task identifier.
        status: Terminal status -- ``"completed"``, ``"failed"``, or
            ``"stopped"``.
        tool_use_id: The tool_use_id of the Task tool invocation.
        summary: Human-readable summary of the task outcome.
        output_file: Path to a file containing the task output.
        usage: Final usage metrics for the task.
    """
    event: TaskNotificationSdkEvent = {
        "type": "system",
        "subtype": "task_notification",
        "task_id": task_id,
        "status": status,
        "output_file": output_file or "",
        "summary": summary or "",
    }
    if tool_use_id:
        event["tool_use_id"] = tool_use_id
    if usage:
        event["usage"] = usage
    enqueue_sdk_event(event)


def emit_task_terminated_sdk(
    task_id: str,
    status: Literal["completed", "failed", "stopped"],
    *,
    tool_use_id: str | None = None,
    summary: str | None = None,
    output_file: str | None = None,
    usage: TaskProgressUsage | None = None,
) -> None:
    """Emit task_notification when a task reaches a terminal state.

    Legacy alias for :func:`emit_task_notification_sdk`.  Kept for
    backwards compatibility with the original TypeScript API surface.

    registerTask() always emits task_started; this is the closing bookend.
    Call this from any exit path that sets a task terminal WITHOUT going
    through enqueuePendingNotification-with-<task-id> (print.ts parses that
    XML into the same SDK event, so paths that do both would double-emit).
    Paths that suppress the XML notification (notified:true pre-set, kill
    paths, abort branches) must call this directly so SDK consumers
    (Scuttle's bg-task dot, VS Code subagent panel) see the task close.
    """
    emit_task_notification_sdk(
        task_id=task_id,
        status=status,
        tool_use_id=tool_use_id,
        summary=summary,
        output_file=output_file,
        usage=usage,
    )


def emit_session_state_changed_sdk(
    state: Literal["idle", "running", "requires_action"],
) -> None:
    """Emit session_state_changed to signal a transition in session state.

    The ``"idle"`` transition fires AFTER heldBackResult flushes and the
    background-agent do-while loop exits -- so SDK consumers can trust it
    as the authoritative "turn is over" signal even when result was
    withheld for background agents.

    Args:
        state: The new session state.
    """
    enqueue_sdk_event(
        SessionStateChangedEvent(
            type="system",
            subtype="session_state_changed",
            state=state,
        )
    )


# ---------------------------------------------------------------------------
# Reset (useful for testing)
# ---------------------------------------------------------------------------


def reset_queue() -> None:
    """Fully reset the queue, stats, listeners, and policy to defaults.

    This is primarily intended for test teardown.  In production code,
    use :func:`clear_sdk_events` to discard events or
    :func:`drain_sdk_events` to retrieve them.
    """
    global _queue, _listeners, _overflow_policy, _max_queue_size, _stats
    global _history, _on_queue_full_callbacks
    with _lock:
        _queue.clear()
        _history.clear()
        _listeners.clear()
        _on_queue_full_callbacks.clear()
        _overflow_policy = OverflowPolicy.DROP_OLDEST
        _max_queue_size = DEFAULT_MAX_QUEUE_SIZE
        _stats = {
            "enqueued": 0,
            "drained": 0,
            "dropped": 0,
            "rejected": 0,
            "cleared": 0,
            "removed": 0,
            "coalesced": 0,
        }
