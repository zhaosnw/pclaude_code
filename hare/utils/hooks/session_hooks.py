"""Port of: src/utils/hooks/sessionHooks.ts"""

from __future__ import annotations

import time
from typing import Any, Callable

# Session-scoped hook store: session_id -> list of hooks
_session_hooks: dict[str, list[dict[str, Any]]] = {}

# Function hooks (TypeScript callback hooks): id -> {callback, timeout, errorMessage}
_function_hooks: dict[str, dict[str, Any]] = {}

# Default session ID (when no session scoping is provided)
_DEFAULT_SESSION = "__default__"


def add_session_hook(
    hook: dict[str, Any],
    session_id: str | None = None,
) -> str:
    """Register a session hook. Returns the hook's matcher key for later removal."""
    sid = session_id or _DEFAULT_SESSION
    hook_id = hook.get("matcher") or hook.get("event", "") + "_" + str(time.time())
    hook["_id"] = hook_id
    hook["_registered_at"] = time.time()
    _session_hooks.setdefault(sid, []).append(hook)
    return hook_id


def remove_session_hook(
    hook_id: str,
    session_id: str | None = None,
) -> bool:
    """Remove a session hook by its matcher key."""
    sid = session_id or _DEFAULT_SESSION
    hooks = _session_hooks.get(sid, [])
    for i, h in enumerate(hooks):
        if h.get("_id") == hook_id or h.get("matcher") == hook_id:
            hooks.pop(i)
            return True
    return False


def clear_session_hooks(session_id: str | None = None) -> None:
    """Clear all hooks for a session (or the default if None)."""
    if session_id is None:
        _session_hooks.pop(_DEFAULT_SESSION, None)
    else:
        _session_hooks.pop(session_id, None)


def get_session_hooks(session_id: str | None = None) -> list[dict[str, Any]]:
    """Get hooks for a session."""
    sid = session_id or _DEFAULT_SESSION
    return list(_session_hooks.get(sid, []))


def add_function_hook(
    callback: Callable[..., Any],
    timeout_ms: int = 30000,
    error_message: str = "Function hook failed",
    session_id: str | None = None,
) -> str:
    """Register a function callback hook. Returns hook ID for removal."""
    hid = f"fn_{time.time()}_{id(callback)}"
    _function_hooks[hid] = {
        "callback": callback,
        "timeoutMs": timeout_ms,
        "errorMessage": error_message,
        "sessionId": session_id or _DEFAULT_SESSION,
        "registeredAt": time.time(),
    }
    return hid


def remove_function_hook(hook_id: str) -> bool:
    """Remove a function hook by its ID."""
    return _function_hooks.pop(hook_id, None) is not None


def get_session_function_hooks(
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get all function hooks for a session."""
    sid = session_id or _DEFAULT_SESSION
    return [h for h in _function_hooks.values() if h.get("sessionId") == sid]
