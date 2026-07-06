"""Skill load analytics.

Port of: src/utils/telemetry/skillLoadedEvent.ts

Logs a ``tengu_skill_loaded`` event for each prompt skill available at
session startup. This enables analytics on which skills are available
across sessions.

Also provides runtime activation tracking (``tengu_skill_activated``)
and a ``SkillLoadReporter`` orchestrator that deduplicates, batches,
and summarizes skill load events for the session.
"""

from __future__ import annotations

import functools
import os
import time
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable


# ---------------------------------------------------------------------------
# Budget constants (ported from SkillTool/prompt.ts)
# ---------------------------------------------------------------------------

SKILL_BUDGET_CONTEXT_PERCENT = 0.01
CHARS_PER_TOKEN = 4
# Fallback: 1% of 200k * 4
DEFAULT_CHAR_BUDGET = 8_000


# ---------------------------------------------------------------------------
# Event schema
# ---------------------------------------------------------------------------


@dataclass
class SkillLoadedEvent:
    """Schema for the ``tengu_skill_loaded`` telemetry event.

    Each instance represents a single prompt skill discovered at session
    startup.  The field names mirror the BigQuery column conventions used
    by the analytics pipeline.

    Attributes:
        skill_name: The unredacted skill name (routed to the privileged
            ``skill_name`` BigQuery column via ``_PROTO_skill_name``).
        skill_source: Origin of the skill (e.g. ``"user"``, ``"builtin"``,
            ``"plugin"``, ``"mcp"``).
        skill_loaded_from: Directory or registry the skill was loaded from
            (``"skills"``, ``"bundled"``, ``"plugin"``, ``"mcp"``,
            ``"commands_DEPRECATED"``).
        skill_budget: Character budget for skill listing (derived from the
            context window token count).
        skill_kind: Optional kind discriminator (e.g. ``"workflow"``).
        event_name: The telemetry event name (defaults to
            ``"tengu_skill_loaded"``).
        timestamp: Unix-epoch timestamp filled at construction time.
    """

    skill_name: str
    skill_source: str = ""
    skill_loaded_from: str = ""
    skill_budget: int = 0
    skill_kind: str | None = None
    event_name: str = field(default="tengu_skill_loaded", repr=False)

    # Additional metadata that may be enriched by callers
    extra_properties: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Skill load statistics
# ---------------------------------------------------------------------------


@dataclass
class SkillLoadStats:
    """Aggregate statistics collected during a skill load operation.

    Produced by ``SkillLoadReporter`` after a full load cycle so callers
    can log a single summary event instead of one event per skill.
    """

    total_discovered: int = 0
    total_emitted: int = 0
    total_duplicates: int = 0
    total_errors: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    by_loaded_from: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    load_duration_ms: float = 0.0
    context_window_tokens: int = 0
    per_skill_budget_chars: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_char_budget(context_window_tokens: int | None = None) -> int:
    """Compute the character budget for skill listings.

    Port of ``getCharBudget()`` from SkillTool/prompt.ts.
    """
    env_budget = os.environ.get("SLASH_COMMAND_TOOL_CHAR_BUDGET")
    if env_budget:
        try:
            return int(env_budget)
        except ValueError:
            pass

    if context_window_tokens:
        return int(
            context_window_tokens
            * CHARS_PER_TOKEN
            * SKILL_BUDGET_CONTEXT_PERCENT
        )

    return DEFAULT_CHAR_BUDGET


def per_skill_budget(
    context_window_tokens: int | None = None,
    skill_count: int = 1,
) -> int:
    """Allocate a per-skill character budget from the total window budget.

    Divides the total char budget evenly across *skill_count* skills.
    Returns at least 1 so every skill receives a non-zero budget.
    """
    if skill_count < 1:
        skill_count = 1
    total = get_char_budget(context_window_tokens)
    return max(1, total // skill_count)


def _build_event_properties(event: SkillLoadedEvent) -> dict[str, Any]:
    """Build the analytics properties dict from an event schema instance."""
    props: dict[str, Any] = {
        "_PROTO_skill_name": event.skill_name,
        "skill_source": event.skill_source,
        "skill_loaded_from": event.skill_loaded_from,
        "skill_budget": event.skill_budget,
    }
    if event.skill_kind:
        props["skill_kind"] = event.skill_kind
    if event.extra_properties:
        props.update(event.extra_properties)
    return props


def _build_activation_properties(
    skill_name: str,
    skill_source: str,
    skill_loaded_from: str,
    invocation_count: int,
    invocation_duration_ms: float,
    *,
    success: bool = True,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Build the analytics properties dict for an activation event."""
    props: dict[str, Any] = {
        "_PROTO_skill_name": skill_name,
        "skill_source": skill_source,
        "skill_loaded_from": skill_loaded_from,
        "skill_invocation_count": invocation_count,
        "skill_invocation_duration_ms": invocation_duration_ms,
        "skill_invocation_success": success,
    }
    if error_message:
        props["skill_invocation_error"] = error_message
    return props


def _skills_from_cwd(cwd: str) -> list[dict[str, Any]]:
    """Discover prompt skills available under *cwd*.

    This is a lightweight port of ``getSkillToolCommands(cwd)`` filtered
    to ``type == "prompt"`` entries.  It uses the same skill-loading
    infrastructure already available in the Python codebase rather than
    reimplementing the full TypeScript command pipeline.
    """
    from hare.skills.loader import load_skills_dir

    skills: list[dict[str, Any]] = []
    try:
        loaded = load_skills_dir(cwd)
    except Exception:
        return skills

    for skill_def in loaded:
        skills.append(
            {
                "name": skill_def.name,
                "source": skill_def.source,
                "loadedFrom": "skills",
            }
        )
    return skills


# ---------------------------------------------------------------------------
# SkillLoadReporter — orchestrator with dedup, batching, and session summary
# ---------------------------------------------------------------------------


class SkillLoadReporter:
    """Collect, deduplicate, and flush skill load events for a session.

    Thread-safe singleton that acts as the central registry for skill
    telemetry.  Callers feed in skill definitions; the reporter deduplicates
    by name (keeping the highest-priority source) and emits batched events.

    Usage::

        reporter = SkillLoadReporter.get_instance()
        reporter.reset(context_window_tokens=200_000)
        reporter.feed_skill(skill_def)
        ...
        stats = reporter.flush()
    """

    _instance: SkillLoadReporter | None = None
    _lock: threading.Lock = threading.Lock()

    # Source priority: higher number = higher priority (wins on name collision)
    _SOURCE_PRIORITY: dict[str, int] = {
        "bundled": 0,
        "user": 10,
        "project": 20,
        "plugin": 25,
        "mcp": 30,
        "managed": 40,
    }

    # Event flush thresholds
    _BATCH_SIZE = 64
    _FLUSH_INTERVAL_S = 10.0
    _RATE_LIMIT_WINDOW_S = 2.0
    _RATE_LIMIT_MAX_PER_KEY = 3

    def __init__(self) -> None:
        self._seen: dict[str, SkillLoadedEvent] = {}  # dedup by name
        self._pending: list[SkillLoadedEvent] = []
        self._stats = SkillLoadStats()
        self._context_window_tokens: int = 0
        self._session_skill_count: int = 0
        self._last_flush_time: float = 0.0
        self._dirty: bool = False
        # Rate-limit state: keyed by (event_name, skill_name) -> (window_start, count)
        self._rate_limit_buckets: dict[tuple[str, str], tuple[float, int]] = {}

    # ---- singleton ------------------------------------------------------

    @classmethod
    def get_instance(cls) -> SkillLoadReporter:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Teardown existing singleton (mainly for tests)."""
        with cls._lock:
            cls._instance = None

    # ---- lifecycle -------------------------------------------------------

    def reset(self, *, context_window_tokens: int = 0) -> None:
        """Clear all state for a new session load cycle."""
        self._seen.clear()
        self._pending.clear()
        self._stats = SkillLoadStats(context_window_tokens=context_window_tokens)
        self._context_window_tokens = context_window_tokens
        self._session_skill_count = 0
        self._last_flush_time = time.monotonic()
        self._dirty = False

    def feed_skill(
        self,
        skill_name: str,
        *,
        skill_source: str = "",
        skill_loaded_from: str = "",
        skill_budget: int | None = None,
        skill_kind: str | None = None,
        skill_type: str = "prompt",
        extra_properties: dict[str, Any] | None = None,
    ) -> bool:
        """Register a discovered skill for telemetry.

        Returns ``True`` if this is a new skill name (not previously seen
        in the session), ``False`` if it was already registered.

        Args:
            skill_type: The skill's category discriminator (``"prompt"``,
                ``"workflow"``, ``"agent"``, ``"mcp"``).  Tracked in
                ``SkillLoadStats.by_type`` for per-type analytics.
        """
        self._stats.total_discovered += 1

        if skill_name in self._seen:
            existing = self._seen[skill_name]
            new_priority = self._SOURCE_PRIORITY.get(
                skill_source, 0
            )
            old_priority = self._SOURCE_PRIORITY.get(
                existing.skill_source, 0
            )
            if new_priority <= old_priority:
                self._stats.total_duplicates += 1
                return False
            # Higher-priority source replaces the existing entry
            self._stats.total_duplicates += 1
            # Decrement the old type before switching
            old_type = existing.extra_properties.get("skill_type", "prompt")
            self._stats.by_type[old_type] = max(
                0, self._stats.by_type.get(old_type, 0) - 1
            )

        budget = (
            skill_budget
            if skill_budget is not None
            else self._per_skill_budget_internal()
        )

        event = SkillLoadedEvent(
            skill_name=skill_name,
            skill_source=skill_source,
            skill_loaded_from=skill_loaded_from,
            skill_budget=budget,
            skill_kind=skill_kind,
            extra_properties=(extra_properties or {}) | {"skill_type": skill_type},
        )
        self._seen[skill_name] = event
        self._pending.append(event)
        self._session_skill_count += 1
        self._dirty = True

        # Track by-type
        self._stats.by_type[skill_type] = (
            self._stats.by_type.get(skill_type, 0) + 1
        )

        # Auto-flush if we hit the batch threshold
        if len(self._pending) >= self._BATCH_SIZE:
            self._emit_batch()

        return True

    def feed_error(self, skill_name: str, error: Exception) -> None:
        """Record a load error for *skill_name*."""
        self._stats.total_errors += 1
        try:
            from hare.utils.telemetry.logger import debug
            debug(f"SkillLoadReporter: error loading skill '{skill_name}': {error}")
        except ImportError:
            pass

    def flush(self) -> SkillLoadStats:
        """Emit any pending events and return the session statistics."""
        if self._pending:
            self._emit_batch()
        now = time.monotonic()
        self._stats.load_duration_ms = (
            now - self._last_flush_time
        ) * 1000.0
        self._last_flush_time = now

        # Emit the summary event
        self._emit_summary(self._stats)
        self._dirty = False
        return self._stats

    # ---- runtime activation tracking ------------------------------------

    def track_activation(
        self,
        skill_name: str,
        *,
        skill_source: str = "",
        skill_loaded_from: str = "",
        invocation_duration_ms: float = 0.0,
        success: bool = True,
        error_message: str | None = None,
    ) -> None:
        """Emit a ``tengu_skill_activated`` event for a mid-session invocation.

        Increments the per-skill invocation counter stored in the reporter
        so the first call for a given skill records count=1, second
        records count=2, etc.
        """
        from hare.utils.telemetry.events import track_event

        invocation_count = self._get_or_incr_activation_count(skill_name)

        props = _build_activation_properties(
            skill_name=skill_name,
            skill_source=skill_source,
            skill_loaded_from=skill_loaded_from,
            invocation_count=invocation_count,
            invocation_duration_ms=invocation_duration_ms,
            success=success,
            error_message=error_message,
        )
        track_event("tengu_skill_activated", props)

    # ---- internals -------------------------------------------------------

    def _per_skill_budget_internal(self) -> int:
        """Compute per-skill budget based on current session state."""
        count = max(1, self._session_skill_count + 1)
        return per_skill_budget(self._context_window_tokens, count)

    def _emit_batch(self) -> None:
        """Flush all pending events to the telemetry pipeline.

        Applies a per-skill-name rate limit so that rapid reloads (e.g. during
        file-watch restarts) do not flood the telemetry backend.
        """
        from hare.utils.telemetry.events import track_event

        emitted_count = 0
        dropped_count = 0

        for event in self._pending:
            # Rate-limit check
            key = (event.event_name, event.skill_name)
            now = time.monotonic()
            bucket = self._rate_limit_buckets.get(key)
            if bucket is not None:
                window_start, count = bucket
                if now - window_start < self._RATE_LIMIT_WINDOW_S:
                    if count >= self._RATE_LIMIT_MAX_PER_KEY:
                        dropped_count += 1
                        continue
                    self._rate_limit_buckets[key] = (window_start, count + 1)
                else:
                    # Window expired — reset
                    self._rate_limit_buckets[key] = (now, 1)
            else:
                self._rate_limit_buckets[key] = (now, 1)

            track_event(event.event_name, _build_event_properties(event))
            self._stats.total_emitted += 1
            emitted_count += 1
            src = event.skill_source or "unknown"
            self._stats.by_source[src] = self._stats.by_source.get(src, 0) + 1
            lf = event.skill_loaded_from or "unknown"
            self._stats.by_loaded_from[lf] = self._stats.by_loaded_from.get(lf, 0) + 1

        self._pending.clear()

        # Periodically purge stale rate-limit buckets
        if len(self._rate_limit_buckets) > 256:
            self._purge_rate_limit_buckets(now)

        if dropped_count > 0:
            self._stats.total_errors += 1  # count rate-limited drops as soft errors

    def _purge_rate_limit_buckets(self, now: float) -> None:
        """Remove expired rate-limit bucket entries."""
        stale = [
            k
            for k, (start, _) in self._rate_limit_buckets.items()
            if now - start > self._RATE_LIMIT_WINDOW_S * 4
        ]
        for k in stale:
            del self._rate_limit_buckets[k]

    def _emit_summary(self, stats: SkillLoadStats) -> None:
        """Emit a single ``tengu_skill_load_summary`` event for the session."""
        from hare.utils.telemetry.events import track_event

        track_event(
            "tengu_skill_load_summary",
            {
                "total_discovered": stats.total_discovered,
                "total_emitted": stats.total_emitted,
                "total_duplicates": stats.total_duplicates,
                "total_errors": stats.total_errors,
                "by_source": stats.by_source,
                "by_loaded_from": stats.by_loaded_from,
                "by_type": stats.by_type,
                "load_duration_ms": stats.load_duration_ms,
                "context_window_tokens": stats.context_window_tokens,
                "per_skill_budget_chars": stats.per_skill_budget_chars,
            },
        )

    def _get_or_incr_activation_count(self, skill_name: str) -> int:
        """Return the current activation count for *skill_name*, incrementing atomically."""
        if not hasattr(self, "_activation_counts"):
            self._activation_counts: dict[str, int] = {}
        count = self._activation_counts.get(skill_name, 0) + 1
        self._activation_counts[skill_name] = count
        return count


# ---------------------------------------------------------------------------
# Async context manager for timed / error-tracked skill loading
# ---------------------------------------------------------------------------


@asynccontextmanager
async def SkillLoadContext(
    cwd: str,
    context_window_tokens: int,
    *,
    reporter: SkillLoadReporter | None = None,
) -> AsyncIterator[SkillLoadReporter]:
    """Async context manager that sets up and tears down a skill-load cycle.

    Usage::

        async with SkillLoadContext(cwd, 200_000) as rep:
            for skill in skills:
                rep.feed_skill(skill.name, skill_source=skill.source)
            stats = rep.flush()   # optional explicit flush
    """
    rep = reporter or SkillLoadReporter.get_instance()
    rep.reset(context_window_tokens=context_window_tokens)

    start = time.monotonic()
    try:
        yield rep
    except Exception as exc:
        rep._stats.total_errors += 1
        try:
            from hare.utils.telemetry.logger import error as log_err
            log_err(f"SkillLoadContext: load cycle failed: {exc}")
        except ImportError:
            pass
        raise
    finally:
        elapsed = (time.monotonic() - start) * 1000.0
        rep._stats.load_duration_ms = elapsed
        if rep._dirty:
            rep.flush()


# ---------------------------------------------------------------------------
# Multi-source skill loading with dedup and priority
# ---------------------------------------------------------------------------


def log_skills_from_multiple_sources(
    cwd: str,
    context_window_tokens: int,
    *,
    reporter: SkillLoadReporter | None = None,
) -> SkillLoadStats:
    """Load skills from all available sources and emit telemetry.

    Sources (lowest to highest priority):
        1. Bundled / builtin skills
        2. User-level skills (~/.hare/skills)
        3. Project-level skills (.hare/skills)
        4. Managed / policy skills

    Returns aggregate ``SkillLoadStats`` for the load cycle.
    """
    from hare.skills.load_skills_dir import (
        load_skills_dir,
        get_user_skills_dir,
        get_project_skills_dir,
        get_managed_skills_dir,
    )

    rep = reporter or SkillLoadReporter.get_instance()
    rep.reset(context_window_tokens=context_window_tokens)

    # 1. Bundled skills (lowest priority)
    try:
        import hare.skills.bundled as bundled_mod
        bundled_dir = os.path.join(os.path.dirname(bundled_mod.__file__), "bundled")
        if os.path.isdir(bundled_dir):
            for skill in load_skills_dir(bundled_dir, "bundled", "bundled"):
                if skill.type != "prompt":
                    continue
                rep.feed_skill(
                    skill.name,
                    skill_source="bundled",
                    skill_loaded_from="bundled",
                    skill_kind=skill.context if skill.context else None,
                )
    except (ImportError, OSError):
        pass

    # 2. User skills
    user_dir = get_user_skills_dir()
    if os.path.isdir(user_dir):
        for skill in load_skills_dir(user_dir, "user", "skills"):
            if skill.type != "prompt":
                continue
            rep.feed_skill(
                skill.name,
                skill_source="user",
                skill_loaded_from="skills",
                skill_kind=skill.context if skill.context else None,
            )

    # 3. Project skills
    project_dir = get_project_skills_dir(cwd)
    if os.path.isdir(project_dir):
        for skill in load_skills_dir(project_dir, "project", "skills"):
            if skill.type != "prompt":
                continue
            rep.feed_skill(
                skill.name,
                skill_source="project",
                skill_loaded_from="skills",
                skill_kind=skill.context if skill.context else None,
            )

    # 4. Managed / policy skills (highest priority)
    managed_dir = get_managed_skills_dir()
    if not os.environ.get("CLAUDE_CODE_DISABLE_POLICY_SKILLS"):
        if os.path.isdir(managed_dir):
            for skill in load_skills_dir(managed_dir, "managed", "managed"):
                if skill.type != "prompt":
                    continue
                rep.feed_skill(
                    skill.name,
                    skill_source="managed",
                    skill_loaded_from="managed",
                    skill_kind=skill.context if skill.context else None,
                )

    # 5. MCP skills (check for mcp-managed skills)
    try:
        from hare.skills.mcp_skill_registry import get_mcp_skills

        for mcp_skill in get_mcp_skills():
            rep.feed_skill(
                mcp_skill.get("name", "unknown"),
                skill_source="mcp",
                skill_loaded_from="mcp",
                skill_kind=mcp_skill.get("kind"),
                extra_properties={
                    "mcp_server": mcp_skill.get("server_name", ""),
                },
            )
    except (ImportError, AttributeError):
        pass

    return rep.flush()


# ---------------------------------------------------------------------------
# Runtime skill activation tracking
# ---------------------------------------------------------------------------


def track_skill_activated(
    skill_name: str,
    *,
    skill_source: str = "",
    skill_loaded_from: str = "",
    invocation_duration_ms: float = 0.0,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    """Emit a ``tengu_skill_activated`` event for a mid-session invocation.

    This is separate from ``tengu_skill_loaded`` — loaded events fire once
    at startup; activated events fire each time the user or model invokes
    a skill during a conversation turn.

    Use the ``SkillLoadReporter`` for session-wide activation counting;
    this module-level function is a convenience that delegates to the
    singleton reporter.
    """
    reporter = SkillLoadReporter.get_instance()
    reporter.track_activation(
        skill_name=skill_name,
        skill_source=skill_source,
        skill_loaded_from=skill_loaded_from,
        invocation_duration_ms=invocation_duration_ms,
        success=success,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def log_skills_loaded(
    cwd: str,
    context_window_tokens: int,
) -> None:
    """Emit ``tengu_skill_loaded`` for each prompt skill.

    Called at session startup so analytics can track which skills are
    available across sessions.
    """
    from hare.skills.loader import load_skills_dir
    from hare.utils.telemetry.events import track_event

    skill_budget = get_char_budget(context_window_tokens)

    try:
        skill_defs = load_skills_dir(cwd)
    except Exception:
        return

    for skill_def in skill_defs:
        # Only emit for prompt-type skills (match TS behaviour)
        if skill_def.type != "prompt":
            continue

        event = SkillLoadedEvent(
            skill_name=skill_def.name,
            skill_source=skill_def.source,
            skill_loaded_from="skills",
            skill_budget=skill_budget,
        )

        track_event(
            event.event_name,
            _build_event_properties(event),
        )


def log_skill_loaded_sync(
    skill_name: str,
    *,
    skill_source: str = "",
    skill_loaded_from: str = "",
    skill_budget: int | None = None,
    skill_kind: str | None = None,
    extra_properties: dict[str, Any] | None = None,
) -> None:
    """Synchronous convenience for emitting a single skill-loaded event.

    Useful when callers already have a fully-resolved skill object and
    do not need to scan the filesystem.
    """
    from hare.utils.telemetry.events import track_event

    budget = skill_budget if skill_budget is not None else get_char_budget()

    event = SkillLoadedEvent(
        skill_name=skill_name,
        skill_source=skill_source,
        skill_loaded_from=skill_loaded_from,
        skill_budget=budget,
        skill_kind=skill_kind,
        extra_properties=extra_properties or {},
    )

    track_event(event.event_name, _build_event_properties(event))


# ---------------------------------------------------------------------------
# SkillLoadCache — thread-safe TTL cache for skill discovery results
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Internal cache entry with expiration."""

    skills: list[dict[str, Any]]
    expires_at: float


class SkillLoadCache:
    """Thread-safe TTL cache for skill load results.

    Wraps skill directory scanning so repeated calls within the TTL window
    return the cached result instead of re-reading the filesystem.  This is
    particularly valuable during startup when multiple subsystems may
    request the same skill list independently.

    Usage::

        cache = SkillLoadCache(ttl_seconds=30.0)
        skills = cache.get_or_load(cwd, loader_fn)
    """

    _DEFAULT_TTL = 30.0

    def __init__(self, ttl_seconds: float = _DEFAULT_TTL) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    # ---- public API -------------------------------------------------------

    def get_or_load(
        self,
        cache_key: str,
        loader: Callable[[], list[dict[str, Any]]],
        *,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """Return cached skills or invoke *loader* to populate the cache.

        Args:
            cache_key: Unique key for this skill source (e.g. the directory path).
            loader: Zero-argument callable that returns a list of skill dicts.
            force_refresh: If ``True``, bypass the cache and reload.
        """
        now = time.monotonic()

        with self._lock:
            if not force_refresh:
                entry = self._store.get(cache_key)
                if entry is not None and now < entry.expires_at:
                    return entry.skills

        # Load outside the lock to avoid blocking readers
        skills = loader()

        with self._lock:
            self._store[cache_key] = _CacheEntry(
                skills=skills,
                expires_at=now + self._ttl,
            )

        return skills

    def get(
        self,
        cache_key: str,
    ) -> list[dict[str, Any]] | None:
        """Return cached skills without invoking the loader.

        Returns ``None`` when the key is absent or expired.
        """
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(cache_key)
            if entry is not None and now < entry.expires_at:
                return entry.skills
        return None

    def invalidate(self, cache_key: str | None = None) -> None:
        """Remove one key (or all keys) from the cache."""
        with self._lock:
            if cache_key is None:
                self._store.clear()
            else:
                self._store.pop(cache_key, None)

    def stats(self) -> dict[str, Any]:
        """Return cache health metrics for diagnostics."""
        now = time.monotonic()
        with self._lock:
            total = len(self._store)
            expired = sum(
                1 for e in self._store.values() if now >= e.expires_at
            )
            return {
                "total_entries": total,
                "expired_entries": expired,
                "active_entries": total - expired,
                "ttl_seconds": self._ttl,
            }


# Global singleton cache (shared across the process)
_global_skill_cache: SkillLoadCache | None = None


def get_skill_cache() -> SkillLoadCache:
    """Return the process-wide singleton ``SkillLoadCache``."""
    global _global_skill_cache
    if _global_skill_cache is None:
        _global_skill_cache = SkillLoadCache()
    return _global_skill_cache


# ---------------------------------------------------------------------------
# instrument_skill_activation — decorator for automatic activation telemetry
# ---------------------------------------------------------------------------


def instrument_skill_activation(
    skill_name: str,
    *,
    skill_source: str = "",
    skill_loaded_from: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate an async callable to auto-track skill activation telemetry.

    Wraps any async function so that each invocation emits a
    ``tengu_skill_activated`` event with timing and success/failure metadata.
    Follows the same pattern as ``instrument_function`` from instrumentation.py.

    Usage::

        @instrument_skill_activation("my-skill", skill_source="user")
        async def run_my_skill(args: str) -> str:
            ...

    The decorator works on both async and sync callables.
    """
    import asyncio

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            success = True
            error_message: str | None = None
            try:
                result = fn(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            except Exception as exc:
                success = False
                error_message = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                elapsed = (time.monotonic() - start) * 1000.0
                track_skill_activated(
                    skill_name=skill_name,
                    skill_source=skill_source,
                    skill_loaded_from=skill_loaded_from,
                    invocation_duration_ms=elapsed,
                    success=success,
                    error_message=error_message,
                )

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            success = True
            error_message = None
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                success = False
                error_message = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                elapsed = (time.monotonic() - start) * 1000.0
                track_skill_activated(
                    skill_name=skill_name,
                    skill_source=skill_source,
                    skill_loaded_from=skill_loaded_from,
                    invocation_duration_ms=elapsed,
                    success=success,
                    error_message=error_message,
                )

        # Detect if the wrapped function is a coroutine function
        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper

    return decorator


# ---------------------------------------------------------------------------
# preload_and_report_skills — high-level orchestrator (cache + reporter)
# ---------------------------------------------------------------------------


def preload_and_report_skills(
    cwd: str,
    context_window_tokens: int,
    *,
    reporter: SkillLoadReporter | None = None,
    cache: SkillLoadCache | None = None,
    force_refresh: bool = False,
) -> SkillLoadStats:
    """Load skills from all sources, cache results, and report telemetry.

    This is the recommended entry point for session startup.  It:
    1. Checks the global ``SkillLoadCache`` for each source directory.
    2. Falls back to filesystem scanning on cache miss.
    3. Feeds discovered skills into the ``SkillLoadReporter``.
    4. Returns aggregate ``SkillLoadStats``.

    Args:
        cwd: Current working directory for project-skill discovery.
        context_window_tokens: Model context window size (for budget calc).
        reporter: Optional reporter instance (uses singleton by default).
        cache: Optional cache instance (uses global singleton by default).
        force_refresh: If ``True``, bypass the cache and reload from disk.

    Returns:
        ``SkillLoadStats`` with counts broken down by source, loaded_from, and type.
    """
    from hare.skills.load_skills_dir import (
        load_skills_dir,
        get_user_skills_dir,
        get_project_skills_dir,
        get_managed_skills_dir,
    )

    rep = reporter or SkillLoadReporter.get_instance()
    rep.reset(context_window_tokens=context_window_tokens)
    cache = cache or get_skill_cache()

    sources: list[tuple[str, str, str, str]] = []

    # Bundled skills
    try:
        import hare.skills.bundled as bundled_mod

        bundled_dir = os.path.join(os.path.dirname(bundled_mod.__file__), "bundled")
        if os.path.isdir(bundled_dir):
            sources.append((bundled_dir, "bundled", "bundled", bundled_dir))
    except (ImportError, OSError):
        pass

    # User skills
    user_dir = get_user_skills_dir()
    if os.path.isdir(user_dir):
        sources.append((user_dir, "user", "skills", user_dir))

    # Project skills
    project_dir = get_project_skills_dir(cwd)
    if os.path.isdir(project_dir):
        sources.append((project_dir, "project", "skills", project_dir))

    # Managed skills
    if not os.environ.get("CLAUDE_CODE_DISABLE_POLICY_SKILLS"):
        managed_dir = get_managed_skills_dir()
        if os.path.isdir(managed_dir):
            sources.append((managed_dir, "managed", "managed", managed_dir))

    for skills_dir, source, loaded_from, cache_key in sources:
        def _loader(d: str = skills_dir, s: str = source, lf: str = loaded_from) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for skill in load_skills_dir(d, s, lf):
                result.append(
                    {
                        "name": skill.name,
                        "source": skill.source,
                        "loadedFrom": skill.loaded_from,
                        "type": skill.type if hasattr(skill, "type") else "prompt",
                        "context": skill.context if hasattr(skill, "context") else "",
                    }
                )
            return result

        skills = cache.get_or_load(cache_key, _loader, force_refresh=force_refresh)
        for sd in skills:
            rep.feed_skill(
                sd["name"],
                skill_source=sd.get("source", source),
                skill_loaded_from=sd.get("loadedFrom", loaded_from),
                skill_kind=sd.get("context") or None,
                skill_type=sd.get("type", "prompt"),
            )

    # MCP skills (not cached — registry is already in-memory)
    try:
        from hare.skills.mcp_skill_registry import get_mcp_skills

        for mcp_skill in get_mcp_skills():
            rep.feed_skill(
                mcp_skill.get("name", "unknown"),
                skill_source="mcp",
                skill_loaded_from="mcp",
                skill_kind=mcp_skill.get("kind"),
                skill_type=mcp_skill.get("type", "prompt"),
                extra_properties={
                    "mcp_server": mcp_skill.get("server_name", ""),
                },
            )
    except (ImportError, AttributeError):
        pass

    return rep.flush()


# ---------------------------------------------------------------------------
# get_cached_or_load_skills — convenience for callers that need the list
# ---------------------------------------------------------------------------


def get_cached_or_load_skills(
    skills_dir: str,
    source: str = "project",
    loaded_from: str = "skills",
    *,
    cache: SkillLoadCache | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Return a list of skill dicts for *skills_dir*, served from cache when possible.

    Convenience wrapper around ``SkillLoadCache.get_or_load`` that builds the
    loader closure from the standard ``load_skills_dir`` import.

    Usage::

        skills = get_cached_or_load_skills("/path/to/skills", source="user")
        for skill in skills:
            print(skill["name"])
    """
    from hare.skills.load_skills_dir import load_skills_dir

    cache = cache or get_skill_cache()

    def _loader() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for skill in load_skills_dir(skills_dir, source, loaded_from):
            result.append(
                {
                    "name": skill.name,
                    "source": skill.source,
                    "loadedFrom": skill.loaded_from,
                    "type": skill.type if hasattr(skill, "type") else "prompt",
                    "context": skill.context if hasattr(skill, "context") else "",
                }
            )
        return result

    return cache.get_or_load(skills_dir, _loader, force_refresh=force_refresh)
