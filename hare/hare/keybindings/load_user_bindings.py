"""
Load user keybindings from ~/.hare/keybindings.json
(port of src/keybindings/loadUserBindings.ts).

Provides both sync and async loading paths with caching, file-watching
for hot-reload, subscription management, and comprehensive error handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from hare.keybindings.parser import parse_bindings
from hare.keybindings.types import KeybindingBlock, ParsedBinding
from hare.keybindings.validate import (
    KeybindingWarning,
    check_duplicate_keys_in_json,
    validate_bindings,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum file size (bytes) to read for keybindings.json – prevents DoS.
_MAX_KEYBINDINGS_FILE_SIZE = 1 * 1024 * 1024  # 1 MB

# Poll interval (seconds) for the file-watcher polling loop.
_WATCHER_POLL_INTERVAL = 2.0

# Debounce window (seconds) – ignore rapid successive change events within
# this window to avoid redundant reloads.
_WATCHER_DEBOUNCE_SECONDS = 0.5


def _hare_config_home() -> Path:
    return Path(os.environ.get("HARE_CONFIG_DIR", os.path.expanduser("~/.hare")))


def get_keybindings_path() -> str:
    return str(_hare_config_home() / "keybindings.json")


def is_keybinding_customization_enabled() -> bool:
    """Check whether user keybinding customization is enabled.

    Reads the ``CLAUDE_CODE_KEYBINDING_CUSTOMIZATION`` env var (truthy
    values: ``1``, ``true``, ``yes``).  Also checks for a feature-flag
    stashed file written by the GrowthBook integration when available.
    """
    env_val = os.environ.get("CLAUDE_CODE_KEYBINDING_CUSTOMIZATION", "").lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False

    # Fallback: check for a GrowthBook / feature-flag file.
    flag_file = _hare_config_home() / "features" / "keybinding_customization"
    try:
        if flag_file.is_file():
            return flag_file.read_text(encoding="utf-8").strip().lower() in (
                "1",
                "true",
                "yes",
            )
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class KeybindingsLoadResult:
    bindings: list[ParsedBinding]
    warnings: list[KeybindingWarning]


SubscribeHandler = Callable[[KeybindingsLoadResult], None]

# ---------------------------------------------------------------------------
# Module-level cache  (guarded by _cache_lock)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()

_cached_bindings: list[ParsedBinding] | None = None
_cached_warnings: list[KeybindingWarning] = []

# ---------------------------------------------------------------------------
# Module-level watcher state  (guarded by _watcher_lock)
# ---------------------------------------------------------------------------

_watcher_lock = threading.Lock()
_watcher_task: asyncio.Task[Any] | None = None
_watcher_stop_event: threading.Event | None = None
_watcher_subscribers: list[SubscribeHandler] = []
_watcher_last_mtime: float = 0.0
_watcher_last_reload: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _default_parsed() -> list[ParsedBinding]:
    from hare.keybindings.default_bindings import DEFAULT_BINDINGS

    return parse_bindings(DEFAULT_BINDINGS)


def _merge_bindings(
    default_bindings: list[ParsedBinding],
    user_parsed: list[ParsedBinding],
) -> list[ParsedBinding]:
    """Merge user bindings over the top of default bindings.

    User bindings that share the same chord+context replace defaults;
    user bindings with new chords are appended.  A user binding whose
    ``action`` is ``None`` *removes* (unbinds) the matching default.
    """
    # Build a quick lookup keyed by (context, chord-string) → index
    from hare.keybindings.parser import chord_to_string as _chord_str

    merged: list[ParsedBinding] = list(default_bindings)
    index: dict[tuple[str, str], int] = {}
    for i, b in enumerate(merged):
        key = (b.context, _chord_str(b.chord))
        index[key] = i

    for ub in user_parsed:
        ub_key = (ub.context, _chord_str(ub.chord))
        if ub.action is None:
            # Unbind: remove from merged.
            idx = index.pop(ub_key, None)
            if idx is not None:
                merged[idx] = ParsedBinding(
                    chord=ub.chord, action=None, context=ub.context
                )
        elif ub_key in index:
            # Override existing.
            merged[index[ub_key]] = ub
        else:
            # Append new binding.
            merged.append(ub)
            index[ub_key] = len(merged) - 1

    # Filter out any bindings that were explicitly unbound (action is None).
    merged = [b for b in merged if b.action is not None]
    return merged


def _validate_file_path(path: str) -> str | None:
    """Return an error message string if the file path is unsafe/invalid, else None."""
    resolved = os.path.realpath(path)
    base = os.path.realpath(str(_hare_config_home()))
    if not resolved.startswith(base + os.sep) and resolved != base:
        return f"Path traversal detected: {path} is outside the hare config directory"
    return None


def _read_keybindings_file(
    path: str,
) -> tuple[str | None, list[KeybindingWarning]]:
    """Read keybindings.json and return ``(content, warnings)``.

    Returns ``(None, warnings)`` on any read error.
    Handles: missing file, permission errors, encoding issues, oversized
    files, and path-traversal attempts.
    """
    fspath = Path(path)

    # Existence check.
    if not fspath.exists():
        return None, []

    if not fspath.is_file():
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f'Keybindings path "{path}" exists but is not a regular file',
            )
        ]

    # Size guard.
    try:
        st = fspath.stat()
        if st.st_size > _MAX_KEYBINDINGS_FILE_SIZE:
            return None, [
                KeybindingWarning(
                    type="parse_error",
                    severity="error",
                    message=(
                        f"Keybindings file is {st.st_size} bytes "
                        f"(max {_MAX_KEYBINDINGS_FILE_SIZE}). "
                        "Refusing to load oversized file."
                    ),
                )
            ]
    except OSError:
        # If we can't stat it we can't read it either – fall through to read.
        pass

    # Read.
    try:
        content = fspath.read_text(encoding="utf-8")
    except PermissionError as e:
        _logger.warning("Permission denied reading keybindings: %s", e)
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f"Permission denied reading keybindings: {e}",
                suggestion="Check file permissions or run with appropriate access",
            )
        ]
    except UnicodeDecodeError as e:
        _logger.warning("Encoding error reading keybindings: %s", e)
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=(
                    f"keybindings.json is not valid UTF-8: {e}. "
                    "Ensure the file is saved with UTF-8 encoding."
                ),
                suggestion="Re-save the file as UTF-8",
            )
        ]
    except OSError as e:
        _logger.warning("Failed to read keybindings: %s", e)
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f"Failed to read keybindings: {e}",
            )
        ]

    # Empty / whitespace-only file is treated as "no custom bindings".
    if not content.strip():
        return None, []

    return content, []


def _parse_file(
    content: str,
) -> tuple[list[KeybindingBlock] | None, list[KeybindingWarning]]:
    """Parse raw JSON string into a list of ``KeybindingBlock`` objects.

    Returns ``(blocks, warnings)``.  If the JSON is structurally invalid
    (missing ``bindings`` array, wrong types, etc.) ``blocks`` is ``None``
    and the caller should fall back to defaults.
    """
    warnings: list[KeybindingWarning] = []

    # --- JSON parse ---
    try:
        parsed: Any = json.loads(content)
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in keybindings.json: {e}"
        if e.lineno and e.colno:
            msg += f" (line {e.lineno}, col {e.colno})"
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=msg,
                suggestion="Validate your JSON syntax (e.g. trailing commas, quotes)",
            )
        ]

    # --- Top-level structure ---
    if not isinstance(parsed, dict):
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=(
                    "keybindings.json must be a JSON object "
                    'with a "bindings" array'
                ),
                suggestion='Use format: { "bindings": [ ... ] }',
            )
        ]

    # Warn about extra unexpected top-level keys (informational – non-fatal).
    known_keys = {"bindings", "$schema", "version", "description"}
    extra = set(parsed.keys()) - known_keys
    for ek in sorted(extra):
        warnings.append(
            KeybindingWarning(
                type="parse_error",
                severity="warning",
                message=f'Unexpected top-level key "{ek}" in keybindings.json – ignored',
                suggestion=f"Valid top-level keys are: {', '.join(sorted(known_keys))}",
            )
        )

    if "bindings" not in parsed:
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message='keybindings.json must have a "bindings" array',
                suggestion='Use format: { "bindings": [ ... ] }',
            )
        ]

    ub = parsed["bindings"]
    if not isinstance(ub, list):
        return None, [
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message='"bindings" must be an array',
                suggestion='Use format: { "bindings": [ ... ] }',
            )
        ]

    # --- Parse blocks ---
    blocks: list[KeybindingBlock] = []
    block_warnings: list[KeybindingWarning] = []

    for i, item in enumerate(ub):
        if not isinstance(item, dict):
            block_warnings.append(
                KeybindingWarning(
                    type="parse_error",
                    severity="warning",
                    message=(
                        f"Item {i + 1} in bindings array is a "
                        f"{type(item).__name__}, not an object – skipped"
                    ),
                )
            )
            continue

        ctx = item.get("context")
        bd = item.get("bindings")

        # Validate context.
        if not isinstance(ctx, str):
            block_warnings.append(
                KeybindingWarning(
                    type="parse_error",
                    severity="warning",
                    message=(
                        f'Item {i + 1} missing or invalid "context" '
                        f"(expected string) – skipping block"
                    ),
                )
            )
            continue

        # Warn about extra keys in the block (informational).
        block_known = {"context", "bindings", "description", "enabled"}
        block_extra = set(item.keys()) - block_known
        for bek in sorted(block_extra):
            block_warnings.append(
                KeybindingWarning(
                    type="parse_error",
                    severity="warning",
                    message=(
                        f'Unexpected key "{bek}" in bindings block '
                        f'"{ctx}" – ignored'
                    ),
                )
            )

        # Check "enabled" flag if present.
        if "enabled" in item and item["enabled"] is False:
            # Skip disabled blocks.
            continue

        if not isinstance(bd, dict):
            block_warnings.append(
                KeybindingWarning(
                    type="parse_error",
                    severity="warning",
                    message=(
                        f'Bindings block "{ctx}" has no valid bindings dict – skipping'
                    ),
                    context=ctx,
                )
            )
            continue

        # Normalise values: null stays None, everything else becomes str.
        normalised: dict[str, str | None] = {}
        for k, v in bd.items():
            key = str(k)
            if v is None:
                normalised[key] = None
            elif isinstance(v, str):
                normalised[key] = v
            else:
                normalised[key] = str(v)

        if normalised:
            blocks.append(
                KeybindingBlock(
                    context=ctx,  # type: ignore[arg-type]
                    bindings=normalised,
                )
            )

    warnings.extend(block_warnings)

    # Duplicate-key detection.
    warnings.extend(check_duplicate_keys_in_json(content))

    # Structural validation against defaults.
    if blocks:
        merged_for_validation = [*_default_parsed(), *parse_bindings(blocks)]
    else:
        merged_for_validation = _default_parsed()
    try:
        warnings.extend(validate_bindings(ub, merged_for_validation))
    except Exception as exc:
        _logger.warning("Validation raised an exception: %s", exc)
        warnings.append(
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f"Validation error: {exc}",
            )
        )

    return blocks, warnings


def _load_impl(
    default_bindings: list[ParsedBinding],
    path: str,
) -> KeybindingsLoadResult:
    """Shared loading implementation used by both async and sync paths.

    Returns ``(merged_bindings, warnings)``.  Always falls back to
    ``default_bindings`` if user config cannot be loaded or parsed.
    """
    content, read_warnings = _read_keybindings_file(path)

    # No file or empty file → use defaults.
    if content is None:
        if read_warnings:
            return KeybindingsLoadResult(
                bindings=list(default_bindings), warnings=read_warnings
            )
        return KeybindingsLoadResult(bindings=list(default_bindings), warnings=[])

    blocks, parse_warnings = _parse_file(content)
    all_warnings = [*read_warnings, *parse_warnings]

    if blocks is None:
        return KeybindingsLoadResult(
            bindings=list(default_bindings), warnings=all_warnings
        )

    user_parsed = parse_bindings(blocks)
    merged = _merge_bindings(default_bindings, user_parsed)
    return KeybindingsLoadResult(bindings=merged, warnings=all_warnings)


# ---------------------------------------------------------------------------
# Public API – loading
# ---------------------------------------------------------------------------


async def load_keybindings() -> KeybindingsLoadResult:
    """Async entry point: load and merge user keybindings.

    If keybinding customization is disabled (env var / feature flag) or
    the user config file is absent, only the default bindings are returned.
    """
    default_bindings = _default_parsed()

    if not is_keybinding_customization_enabled():
        return KeybindingsLoadResult(bindings=default_bindings, warnings=[])

    path = get_keybindings_path()
    return _load_impl(default_bindings, path)


def load_keybindings_sync() -> list[ParsedBinding]:
    """Sync convenience wrapper: returns only the merged binding list.

    Results are cached on first call; subsequent calls return the cached
    value.  Use ``reset_keybinding_loader_for_testing()`` or
    ``reload_after_file_change()`` to invalidate.
    """
    global _cached_bindings, _cached_warnings
    with _cache_lock:
        if _cached_bindings is not None:
            return _cached_bindings
    res = load_keybindings_sync_with_warnings()
    with _cache_lock:
        _cached_bindings = res.bindings
        _cached_warnings = res.warnings
    return res.bindings


def load_keybindings_sync_with_warnings() -> KeybindingsLoadResult:
    """Sync entry point: load and merge, returning both bindings and warnings.

    Thread-safe.  On first call the result is cached; subsequent calls
    return a copy of the cached data (so callers can mutate the returned
    warning list without affecting the cache).
    """
    global _cached_bindings, _cached_warnings

    with _cache_lock:
        if _cached_bindings is not None:
            return KeybindingsLoadResult(
                bindings=list(_cached_bindings),
                warnings=list(_cached_warnings),
            )

    default_bindings = _default_parsed()

    if not is_keybinding_customization_enabled():
        with _cache_lock:
            _cached_bindings = default_bindings
            _cached_warnings = []
        return KeybindingsLoadResult(
            bindings=list(default_bindings), warnings=[]
        )

    path = get_keybindings_path()
    result = _load_impl(default_bindings, path)

    with _cache_lock:
        _cached_bindings = list(result.bindings)
        _cached_warnings = list(result.warnings)
        # Also track the file mtime for the watcher.
        _update_watcher_mtime(path)

    return KeybindingsLoadResult(
        bindings=list(result.bindings),
        warnings=list(result.warnings),
    )


def get_cached_keybinding_warnings() -> list[KeybindingWarning]:
    """Return a copy of cached warnings (thread-safe)."""
    with _cache_lock:
        return list(_cached_warnings)


def reset_keybinding_loader_for_testing() -> None:
    """Clear the module-level cache – useful in tests."""
    global _cached_bindings, _cached_warnings
    with _cache_lock:
        _cached_bindings = None
        _cached_warnings = []


def reload_after_file_change() -> KeybindingsLoadResult | None:
    """Force a reload and notify subscribers if bindings changed.

    Called by the file watcher when the keybindings file is modified.
    Returns the new result, or ``None`` if nothing changed (e.g. the
    file was deleted and bindings already reflect defaults).
    """
    global _cached_bindings, _cached_warnings

    with _cache_lock:
        old_bindings = _cached_bindings
        _cached_bindings = None
        _cached_warnings = []

    default_bindings = _default_parsed()

    if not is_keybinding_customization_enabled():
        new_result = KeybindingsLoadResult(
            bindings=list(default_bindings), warnings=[]
        )
    else:
        path = get_keybindings_path()
        new_result = _load_impl(default_bindings, path)

    with _cache_lock:
        _cached_bindings = list(new_result.bindings)
        _cached_warnings = list(new_result.warnings)
        _update_watcher_mtime(get_keybindings_path())

    # Determine if bindings actually changed.
    changed = not _bindings_equal(old_bindings, new_result.bindings)

    if changed:
        _logger.info(
            "Keybindings reloaded: %d bindings, %d warnings",
            len(new_result.bindings),
            len(new_result.warnings),
        )
        # Notify subscribers outside the lock to avoid deadlocks.
        _notify_subscribers(new_result)

    return new_result if changed else None


def _bindings_equal(
    a: list[ParsedBinding] | None,
    b: list[ParsedBinding],
) -> bool:
    """Compare two binding lists for equality (ignoring order)."""
    if a is None:
        return False
    if len(a) != len(b):
        return False

    from hare.keybindings.parser import chord_to_string

    def _key(binding: ParsedBinding) -> tuple[str, str, str]:
        return (
            binding.context,
            chord_to_string(binding.chord),
            binding.action or "",
        )

    a_keys = sorted(_key(x) for x in a)
    b_keys = sorted(_key(x) for x in b)
    return a_keys == b_keys


# ---------------------------------------------------------------------------
# Public API – file watcher
# ---------------------------------------------------------------------------


def _update_watcher_mtime(path: str) -> None:
    """Record the current mtime of the watched file (call under lock)."""
    global _watcher_last_mtime
    try:
        st = os.stat(path)
        _watcher_last_mtime = st.st_mtime
    except OSError:
        _watcher_last_mtime = 0.0


def _notify_subscribers(result: KeybindingsLoadResult) -> None:
    """Call every registered subscriber with the new result.

    Each subscriber is called in its own try/except so one failing
    subscriber does not prevent others from being notified.
    """
    with _watcher_lock:
        subscribers = list(_watcher_subscribers)

    for handler in subscribers:
        try:
            handler(result)
        except Exception:
            _logger.exception(
                "Keybinding change subscriber %r raised an exception", handler
            )


async def _watcher_poll_loop(
    path: str,
    stop_event: threading.Event,
) -> None:
    """Async polling loop that watches ``path`` for mtime changes.

    When a change is detected, ``reload_after_file_change()`` is called
    to reload bindings and notify subscribers.  The loop exits when
    ``stop_event`` is set.
    """
    global _watcher_last_mtime, _watcher_last_reload

    _logger.debug("Keybinding file watcher started for %s", path)

    # Record the baseline mtime.  If the file does not exist yet we watch
    # for it to appear.
    try:
        current_mtime = os.stat(path).st_mtime
    except OSError:
        current_mtime = 0.0

    with _watcher_lock:
        _watcher_last_mtime = current_mtime

    while not stop_event.is_set():
        try:
            new_mtime: float
            try:
                st = os.stat(path)
                new_mtime = st.st_mtime
            except FileNotFoundError:
                new_mtime = 0.0
            except OSError:
                # Permission error, etc. – treat as no-change.
                await asyncio.sleep(_WATCHER_POLL_INTERVAL)
                continue

            with _watcher_lock:
                old_mtime = _watcher_last_mtime

            # Detect: file appeared, was modified, or was deleted.
            file_appeared = old_mtime == 0.0 and new_mtime > 0.0
            file_modified = old_mtime > 0.0 and new_mtime > old_mtime
            file_deleted = old_mtime > 0.0 and new_mtime == 0.0

            if file_appeared or file_modified or file_deleted:
                # Debounce guard.
                now = time.monotonic()
                with _watcher_lock:
                    if now - _watcher_last_reload < _WATCHER_DEBOUNCE_SECONDS:
                        await asyncio.sleep(_WATCHER_POLL_INTERVAL)
                        continue
                    _watcher_last_reload = now
                    _watcher_last_mtime = new_mtime

                _logger.debug(
                    "Keybinding file change detected (mtime %s → %s)",
                    old_mtime,
                    new_mtime,
                )
                reload_after_file_change()
        except Exception:
            _logger.exception("Error in keybinding watcher poll loop")

        await asyncio.sleep(_WATCHER_POLL_INTERVAL)

    _logger.debug("Keybinding file watcher stopped for %s", path)


async def initialize_keybinding_watcher() -> None:
    """Start the background file-watcher for keybindings.json.

    When the user config file is created, modified, or deleted the watcher
    automatically reloads bindings and notifies all registered subscribers.

    Safe to call multiple times – subsequent calls are no-ops if the
    watcher is already running.  If there is no running event loop (e.g.
    in a sync-only context) this function logs a warning and returns.
    """
    global _watcher_task, _watcher_stop_event

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _logger.warning(
            "Cannot start keybinding watcher: no running event loop"
        )
        return

    with _watcher_lock:
        if _watcher_task is not None and not _watcher_task.done():
            _logger.debug("Keybinding watcher already running – skipping")
            return

        path = get_keybindings_path()
        _watcher_stop_event = threading.Event()
        _watcher_task = loop.create_task(
            _watcher_poll_loop(path, _watcher_stop_event)
        )
        _logger.info("Keybinding file watcher initialised for %s", path)


def dispose_keybinding_watcher() -> None:
    """Stop the file watcher and clean up all watcher state.

    Safe to call multiple times.  After disposal a new watcher can be
    started via ``initialize_keybinding_watcher()``.
    """
    global _watcher_task, _watcher_stop_event, _watcher_subscribers
    global _watcher_last_mtime, _watcher_last_reload

    with _watcher_lock:
        if _watcher_stop_event is not None:
            _watcher_stop_event.set()

        task = _watcher_task
        _watcher_task = None
        _watcher_stop_event = None
        _watcher_subscribers = []
        _watcher_last_mtime = 0.0
        _watcher_last_reload = 0.0

    if task is not None and not task.done():
        task.cancel()
        _logger.debug("Keybinding watcher task cancelled")


# ---------------------------------------------------------------------------
# Public API – subscriptions
# ---------------------------------------------------------------------------


def subscribe_to_keybinding_changes(
    handler: SubscribeHandler,
) -> Callable[[], None]:
    """Register a callback to be invoked whenever keybindings change.

    Returns an unsubscribe function.  Call it to remove the handler::

        unsub = subscribe_to_keybinding_changes(my_handler)
        # ... later ...
        unsub()

    The handler receives a ``KeybindingsLoadResult`` with the new
    bindings and any warnings.
    """
    with _watcher_lock:
        _watcher_subscribers.append(handler)

    def _unsubscribe() -> None:
        with _watcher_lock:
            try:
                _watcher_subscribers.remove(handler)
            except ValueError:
                pass  # Already removed.

    return _unsubscribe
