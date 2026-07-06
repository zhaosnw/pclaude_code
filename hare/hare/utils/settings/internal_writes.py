"""Internal settings writes. Port of internalWrites.ts.

Tracks timestamps of in-process settings-file writes so the settings
change detector can ignore its own echoes.

The map is the only shared state between settings.ts and changeDetector.ts.
"""

from __future__ import annotations

import time
from threading import Lock

_timestamps: dict[str, float] = {}
_lock = Lock()


def mark_internal_write(path: str) -> None:
    """Record that an internal write just happened at `path`.

    The path should be resolved (absolute, normalized) by the caller.
    """
    with _lock:
        _timestamps[path] = time.monotonic()


def consume_internal_write(path: str, window_ms: float) -> bool:
    """True if `path` was marked within `window_ms`.

    Consumes the mark on match — the watcher fires once per write, so a
    matched mark shouldn't suppress the next (real, external) change to
    the same file.
    """
    now = time.monotonic()
    window_s = window_ms / 1000.0
    with _lock:
        ts = _timestamps.get(path)
        if ts is not None and (now - ts) < window_s:
            _timestamps.pop(path, None)
            return True
    return False


def clear_internal_writes() -> None:
    """Clear all tracked internal write timestamps."""
    with _lock:
        _timestamps.clear()
