"""
Port of: src/utils/settings/changeDetector.ts

Detects changes to settings files and notifies subscribers.

Architecture:
- Polling-based file watcher (no chokidar dependency in Python)
- MDM settings polling at 30-minute intervals
- Internal write exclusion to avoid self-triggering
- Deletion grace period for delete-and-recreate patterns
- Signal-based pub/sub with typed Source parameter
- Lifecycle: initialize / dispose / resetForTesting
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from hare.bootstrap.state import get_is_remote_mode
from hare.utils.cleanup_registry import register_cleanup
from hare.utils.settings.constants import SETTING_SOURCES, SettingSource
from hare.utils.settings.internal_writes import (
    clear_internal_writes,
    consume_internal_write,
)
from hare.utils.settings.managed_path import get_managed_settings_drop_in_dir
from hare.utils.settings.settings import (
    get_settings_file_path_for_source,
    reset_settings_cache,
)
# Signal is not used directly here — SettingsChangeSignal is self-contained

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timing constants (matching TS)
# ---------------------------------------------------------------------------

FILE_STABILITY_THRESHOLD_MS = 1000
FILE_STABILITY_POLL_INTERVAL_MS = 500
INTERNAL_WRITE_WINDOW_MS = 5000
MDM_POLL_INTERVAL_MS = 30 * 60 * 1000  # 30 minutes

DELETION_GRACE_MS = (
    FILE_STABILITY_THRESHOLD_MS + FILE_STABILITY_POLL_INTERVAL_MS + 200
)

# File-watcher poll interval (Python polling watcher — no chokidar)
FILE_POLL_INTERVAL_S = max(FILE_STABILITY_POLL_INTERVAL_MS / 1000.0, 0.25)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ConfigChangeSource = str  # e.g. "user_settings", "project_settings", etc.
SettingsChangeListener = Callable[[SettingSource], None]


def _setting_source_to_config_change_source(source: SettingSource) -> ConfigChangeSource:
    """Map SettingSource to ConfigChange hook source name."""
    return {
        "userSettings": "user_settings",
        "projectSettings": "project_settings",
        "localSettings": "local_settings",
        "flagSettings": "policy_settings",
        "policySettings": "policy_settings",
    }.get(source, "policy_settings")


# ---------------------------------------------------------------------------
# SettingsChangeSignal — typed Signal wrapper
# ---------------------------------------------------------------------------


class SettingsChangeSignal:
    """Typed signal that emits (source: SettingSource) to subscribers."""

    def __init__(self) -> None:
        self._typed_listeners: dict[int, SettingsChangeListener] = {}
        self._next_id = 0

    def subscribe(
        self, listener: SettingsChangeListener
    ) -> Callable[[], None]:
        lid = self._next_id
        self._next_id += 1
        self._typed_listeners[lid] = listener

        def unsub() -> None:
            self._typed_listeners.pop(lid, None)

        return unsub

    def emit(self, source: SettingSource) -> None:
        for listener in list(self._typed_listeners.values()):
            try:
                listener(source)
            except Exception:
                _log.exception("Settings change listener error")

    def clear(self) -> None:
        self._typed_listeners.clear()


# ---------------------------------------------------------------------------
# File state tracking for polling-based change detection
# ---------------------------------------------------------------------------


@dataclass
class _FileState:
    path: str
    mtime_ns: int = 0
    size: int = 0
    exists: bool = False
    last_seen: float = 0.0


@dataclass
class _PendingDeletion:
    path: str
    source: SettingSource
    deadline: float  # monotonic time when grace expires


# ---------------------------------------------------------------------------
# SettingsChangeDetector
# ---------------------------------------------------------------------------


@dataclass
class SettingsChangeDetector:
    """Detect settings file changes and notify subscribers.

    Uses polling-based file watching (no external watcher dependency).
    Thread-safe: all mutable state is accessed under _lock.
    """

    # Mutable state (protected by _lock)
    _settings_changed: SettingsChangeSignal = field(default_factory=SettingsChangeSignal)
    _initialized: bool = False
    _disposed: bool = False
    _poll_timer: threading.Thread | None = None
    _stop_poll: threading.Event = field(default_factory=threading.Event)
    _mdm_timer: threading.Timer | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # File state tracking
    _file_states: dict[str, _FileState] = field(default_factory=dict)
    _pending_deletions: dict[str, _PendingDeletion] = field(default_factory=dict)
    _watched_dirs: set[str] = field(default_factory=set)

    # MDM snapshot
    _last_mdm_snapshot: str | None = None

    # Test overrides
    _test_overrides: dict[str, int] = field(default_factory=dict)

    # Unregister handle
    _unregister_cleanup: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._initialized

    @property
    def disposed(self) -> bool:
        with self._lock:
            return self._disposed

    def subscribe(
        self, listener: SettingsChangeListener
    ) -> Callable[[], None]:
        """Subscribe to settings change notifications.

        Returns an unsubscribe function.
        """
        return self._settings_changed.subscribe(listener)

    def initialize(self) -> None:
        """Start watching settings files and MDM polling.

        Idempotent — calling multiple times is safe.
        No-op in remote mode.
        """
        if get_is_remote_mode():
            return
        with self._lock:
            if self._initialized or self._disposed:
                return
            self._initialized = True

        # Register cleanup
        self._unregister_cleanup = register_cleanup(self._make_dispose_async())

        # Start MDM poll
        self._start_mdm_poll()

        # Build watch targets
        self._build_watch_targets()

        if not self._watched_dirs:
            return

        _log.debug(
            "Watching for changes in setting files: %s; drop-in dir: %s",
            list(self._file_states.keys()),
            get_managed_settings_drop_in_dir(),
        )

        # Start polling thread
        self._stop_poll.clear()
        self._poll_timer = threading.Thread(
            target=self._poll_loop, daemon=True, name="settings-watcher"
        )
        self._poll_timer.start()

    def dispose(self) -> None:
        """Stop watching and clean up. Returns immediately; file watcher
        stops asynchronously (next poll tick).
        """
        with self._lock:
            if self._disposed:
                return
            self._disposed = True

        self._stop_poll.set()

        if self._mdm_timer is not None:
            self._mdm_timer.cancel()
            self._mdm_timer = None

        with self._lock:
            self._pending_deletions.clear()
            self._file_states.clear()
            self._watched_dirs.clear()
            self._last_mdm_snapshot = None

        clear_internal_writes()
        self._settings_changed.clear()

        if self._unregister_cleanup:
            self._unregister_cleanup()
            self._unregister_cleanup = None

    def notify_change(self, source: SettingSource) -> None:
        """Programmatically notify of a settings change.

        Used when settings are modified in-process (e.g. remote managed
        settings refresh) rather than via filesystem events.
        """
        _log.debug("Programmatic settings change notification for %s", source)
        self._fan_out(source)

    def reset_for_testing(self, overrides: dict[str, int] | None = None) -> None:
        """Reset internal state for testing.

        Allows re-initialization after dispose(). Optionally accepts
        timing overrides for faster test execution.
        """
        with self._lock:
            if self._mdm_timer is not None:
                self._mdm_timer.cancel()
                self._mdm_timer = None

            self._pending_deletions.clear()
            self._last_mdm_snapshot = None
            self._file_states.clear()
            self._watched_dirs.clear()
            self._initialized = False
            self._disposed = False
            self._test_overrides = overrides or {}

        self._stop_poll.set()
        self._settings_changed.clear()

    # ------------------------------------------------------------------
    # Internal: poll loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Background thread: periodically stat settings files, detect changes."""
        interval = FILE_POLL_INTERVAL_S
        while not self._stop_poll.wait(timeout=interval):
            if self._disposed:
                return
            try:
                self._poll_files()
                self._process_pending_deletions()
            except Exception:
                _log.exception("Settings file poll error")

    def _poll_files(self) -> None:
        """Stat all tracked files; emit change events when mtime/size differ."""
        with self._lock:
            states = dict(self._file_states)

        changes: list[tuple[str, SettingSource]] = []

        for path, state in states.items():
            new_state = self._stat_file(path)
            with self._lock:
                self._file_states[path] = new_state

            if state.exists and not new_state.exists:
                # File deleted — start grace period
                source = self._get_source_for_path(path)
                if source:
                    self._handle_delete(path, source)
            elif not state.exists and new_state.exists:
                # File created — cancel any pending deletion, treat as change
                self._handle_add(path)
            elif (
                state.exists
                and new_state.exists
                and (
                    state.mtime_ns != new_state.mtime_ns
                    or state.size != new_state.size
                )
            ):
                # File modified
                source = self._get_source_for_path(path)
                if source:
                    self._handle_change(path, source)

    def _process_pending_deletions(self) -> None:
        """Check for pending deletions whose grace period has expired."""
        now = time.monotonic()
        expired: list[tuple[str, str]] = []  # (path, source)

        with self._lock:
            for path, pending in list(self._pending_deletions.items()):
                if now >= pending.deadline:
                    expired.append((path, pending.source))

        for path, source in expired:
            with self._lock:
                pending = self._pending_deletions.pop(path, None)
                if pending is None:
                    continue
            self._execute_delete_hooks(path, source)

    # ------------------------------------------------------------------
    # Internal: event handlers
    # ------------------------------------------------------------------

    def _handle_change(self, path: str, source: SettingSource) -> None:
        """Handle a file modification event."""
        # Cancel any pending deletion for this path
        with self._lock:
            pending = self._pending_deletions.pop(path, None)
        if pending:
            _log.debug(
                "Cancelled pending deletion of %s — file was modified", path
            )

        # Check internal write
        if consume_internal_write(path, INTERNAL_WRITE_WINDOW_MS):
            return

        _log.debug("Detected change to %s", path)
        self._execute_change_hooks(path, source)

    def _handle_add(self, path: str) -> None:
        """Handle a file creation event."""
        with self._lock:
            pending = self._pending_deletions.pop(path, None)
        if pending:
            _log.debug(
                "Cancelled pending deletion of %s — file was re-added", path
            )

        source = self._get_source_for_path(path)
        if source:
            self._handle_change(path, source)

    def _handle_delete(self, path: str, source: SettingSource) -> None:
        """Start a grace period for a file deletion.

        If the file is recreated within the grace period (detected via
        _handle_add or _handle_change), the deletion is cancelled.
        """
        _log.debug("Detected deletion of %s", path)

        with self._lock:
            if path in self._pending_deletions:
                return  # already pending
            deadline = time.monotonic() + (
                self._test_overrides.get("deletionGrace", DELETION_GRACE_MS)
                / 1000.0
            )
            self._pending_deletions[path] = _PendingDeletion(
                path=path, source=source, deadline=deadline
            )

    def _execute_change_hooks(self, path: str, source: SettingSource) -> None:
        """Fire ConfigChange hooks, then fan-out to subscribers.

        In the TS version this is async with executeConfigChangeHooks.
        Here we fire hooks synchronously for simplicity; the hook system
        can be made async later.
        """
        config_source = _setting_source_to_config_change_source(source)

        # Attempt to execute config change hooks (best-effort)
        try:
            self._run_config_change_hooks(config_source, path)
        except Exception:
            _log.exception("ConfigChange hook error for %s", path)

        self._fan_out(source)

    def _execute_delete_hooks(self, path: str, source: SettingSource) -> None:
        """Handle an expired deletion grace period."""
        config_source = _setting_source_to_config_change_source(source)

        try:
            self._run_config_change_hooks(config_source, path)
        except Exception:
            _log.exception("ConfigChange hook error for deletion of %s", path)

        self._fan_out(source)

    def _fan_out(self, source: SettingSource) -> None:
        """Reset settings cache, then notify all subscribers.

        The cache reset MUST happen here (single producer), not in each
        listener. See TS fanOut() for rationale.
        """
        reset_settings_cache()
        self._settings_changed.emit(source)

    # ------------------------------------------------------------------
    # Internal: path resolution
    # ------------------------------------------------------------------

    def _get_source_for_path(self, path: str) -> SettingSource | None:
        """Determine which SettingSource a file path belongs to."""
        normalized = os.path.normpath(path)

        # Check drop-in directory first
        drop_in = get_managed_settings_drop_in_dir()
        if normalized.startswith(drop_in + os.sep):
            return "policySettings"

        for source in SETTING_SOURCES:
            source_path = get_settings_file_path_for_source(source)  # type: ignore[arg-type]
            if source_path and os.path.normpath(source_path) == normalized:
                return source  # type: ignore[return-value]

        return None

    def _build_watch_targets(self) -> None:
        """Scan all settings sources and register file paths to watch."""
        with self._lock:
            self._file_states.clear()
            self._watched_dirs.clear()

        for source in SETTING_SOURCES:
            if source == "flagSettings":
                continue  # CLI flags don't change during session

            path = get_settings_file_path_for_source(source)  # type: ignore[arg-type]
            if not path:
                continue

            dirname = os.path.dirname(path)
            with self._lock:
                self._watched_dirs.add(dirname)
                if path not in self._file_states:
                    self._file_states[path] = self._stat_file(path)

        # Also track managed-settings.d/ drop-in dir
        drop_in = get_managed_settings_drop_in_dir()
        try:
            if os.path.isdir(drop_in):
                with self._lock:
                    self._watched_dirs.add(drop_in)
                for entry in os.scandir(drop_in):
                    if entry.is_file() and entry.name.endswith(".json"):
                        fpath = os.path.join(drop_in, entry.name)
                        with self._lock:
                            self._file_states[fpath] = self._stat_file(fpath)
        except OSError:
            pass  # drop-in dir doesn't exist

    @staticmethod
    def _stat_file(path: str) -> _FileState:
        """Stat a single file; return a _FileState snapshot."""
        try:
            st = os.stat(path)
            return _FileState(
                path=path,
                mtime_ns=st.st_mtime_ns,
                size=st.st_size,
                exists=True,
                last_seen=time.monotonic(),
            )
        except FileNotFoundError:
            return _FileState(path=path, exists=False, last_seen=time.monotonic())
        except OSError:
            return _FileState(path=path, exists=False, last_seen=time.monotonic())

    # ------------------------------------------------------------------
    # Internal: MDM polling
    # ------------------------------------------------------------------

    def _start_mdm_poll(self) -> None:
        """Start periodic polling for MDM settings changes.

        MDM settings (macOS plist / Windows registry) can't be watched
        via filesystem events, so we poll at 30-minute intervals.
        """
        self._capture_mdm_snapshot()

        interval_ms = self._test_overrides.get(
            "mdmPollInterval", MDM_POLL_INTERVAL_MS
        )
        self._schedule_mdm_poll(interval_ms / 1000.0)

    def _schedule_mdm_poll(self, interval_s: float) -> None:
        """Schedule the next MDM poll tick."""
        if self._disposed:
            return

        self._mdm_timer = threading.Timer(interval_s, self._mdm_poll_tick)
        self._mdm_timer.daemon = True
        self._mdm_timer.start()

    def _mdm_poll_tick(self) -> None:
        """Run one MDM poll tick; compare against last snapshot."""
        if self._disposed:
            return

        try:
            current = self._capture_mdm_snapshot()
            if self._last_mdm_snapshot is not None and current != self._last_mdm_snapshot:
                self._last_mdm_snapshot = current
                _log.debug("Detected MDM settings change via poll")
                self._fan_out("policySettings")
        except Exception:
            _log.exception("MDM poll error")

        # Reschedule
        interval_ms = self._test_overrides.get(
            "mdmPollInterval", MDM_POLL_INTERVAL_MS
        )
        self._schedule_mdm_poll(interval_ms / 1000.0)

    def _capture_mdm_snapshot(self) -> str:
        """Capture current MDM settings as a JSON string for comparison."""
        import json

        mdm_settings = self._read_mdm_settings()
        snapshot = json.dumps(mdm_settings, sort_keys=True, default=str)
        if self._last_mdm_snapshot is None:
            self._last_mdm_snapshot = snapshot
        return snapshot

    @staticmethod
    def _read_mdm_settings() -> dict[str, Any]:
        """Read current MDM settings based on platform.

        macOS: reads from /Library/Managed Preferences/
        Windows: stub for HKLM/HKCU registry
        Linux: stub for /etc/hare-code/managed-settings.json
        """
        result: dict[str, Any] = {}

        system = platform.system()
        if system == "Darwin":
            pref_path = os.environ.get(
                "HARE_MDM_SETTINGS_PATH",
                "/Library/Managed Preferences/com.anthropic.hare-code.plist",
            )
            if os.path.exists(pref_path):
                try:
                    import plistlib

                    with open(pref_path, "rb") as f:
                        result = dict(plistlib.load(f))
                except Exception:
                    _log.debug("Could not read MDM plist at %s", pref_path)
        elif system == "Windows":
            # Stub: HKCU/HKLM registry reads would go here
            pass
        else:
            # Linux: check managed-settings.json
            managed_path = "/etc/hare-code/managed-settings.json"
            if os.path.exists(managed_path):
                try:
                    import json

                    with open(managed_path, "r", encoding="utf-8") as f:
                        result = json.load(f)
                except Exception:
                    _log.debug(
                        "Could not read managed settings at %s", managed_path
                    )

        return result

    # ------------------------------------------------------------------
    # Internal: config change hooks (stub)
    # ------------------------------------------------------------------

    @staticmethod
    def _run_config_change_hooks(
        config_source: ConfigChangeSource, path: str
    ) -> None:
        """Execute ConfigChange hooks for a settings change.

        This is a stub — the full hook execution engine in TS runs async
        and can block settings changes (exit code 2 or decision: 'block').

        In the Python port, hook execution is best-effort during the
        synchronous poll loop. A full implementation would dispatch to
        the hook runner in hare/utils/hooks/.
        """
        # Stub: In a full implementation this would call the hook engine.
        # See src/utils/hooks/hooksConfigManager.ts for the TS equivalent.
        pass

    # ------------------------------------------------------------------
    # Internal: dispose helpers
    # ------------------------------------------------------------------

    def _make_dispose_async(self):
        """Return an async callable for the cleanup registry."""
        import asyncio

        async def _dispose() -> None:
            self.dispose()

        return _dispose


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_detector: SettingsChangeDetector | None = None
_detector_lock = threading.Lock()


def _get_detector() -> SettingsChangeDetector:
    """Lazily initialized module-level singleton."""
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                _detector = SettingsChangeDetector()
    return _detector


# ---------------------------------------------------------------------------
# Legacy API (backward-compatible with the original stubs)
# ---------------------------------------------------------------------------

# The _listeners list and settings_change_detector() / on_settings_change()
# are kept for backward compatibility with code that may already import them.
# New code should use the SettingsChangeDetector singleton directly.

_legacy_listeners: list[Callable[[], None]] = []


def settings_change_detector() -> None:
    """Fire all legacy listeners (no source argument).

    Kept for backward compatibility. Prefer detector.subscribe().
    """
    for listener in list(_legacy_listeners):
        try:
            listener()
        except Exception:
            _log.exception("Legacy settings change listener error")


def on_settings_change(listener: Callable[[], None]) -> Callable[[], None]:
    """Subscribe a legacy listener (no source argument).

    Kept for backward compatibility. Prefer detector.subscribe().
    Returns an unsubscribe function.
    """
    _legacy_listeners.append(listener)

    def unsub() -> None:
        try:
            _legacy_listeners.remove(listener)
        except ValueError:
            pass

    return unsub


# ---------------------------------------------------------------------------
# Top-level API (matching TS settingsChangeDetector object)
# ---------------------------------------------------------------------------

settings_change_detector_obj = {
    "initialize": lambda: _get_detector().initialize(),
    "dispose": lambda: _get_detector().dispose(),
    "subscribe": lambda listener: _get_detector().subscribe(listener),
    "notifyChange": lambda source: _get_detector().notify_change(source),
    "resetForTesting": lambda overrides=None: _get_detector().reset_for_testing(overrides),
}
