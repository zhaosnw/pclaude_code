"""Shortcut display helpers (port of src/keybindings/shortcutFormat.ts).

Provides non-React shortcut display resolution for commands, services, and
other contexts where React hooks are not available.

The module is separated from useShortcutDisplay so that non-React callers
(like query/stopHooks) do not pull React into their module graph via the
sibling hook.
"""

from __future__ import annotations

import platform as _platform
from typing import Optional

from hare.keybindings.load_user_bindings import (
    load_keybindings_sync,
    reset_keybinding_loader_for_testing,
)
from hare.keybindings.parser import (
    chord_to_display_string,
    chord_to_string,
)
from hare.keybindings.resolver import get_binding_display_text
from hare.keybindings.types import KeybindingContextName
from hare.services.analytics import log_event

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

# Track which action+context pairs have already logged a fallback event
# to avoid duplicate events from repeated calls in non-React contexts.
_LOGGED_FALLBACKS: set[str] = set()

# Per-process display cache.  Keyed by "action:context" so that repeated
# calls for the same action+context pair hit the cache rather than
# re-parsing and re-resolving the whole keybindings list each time.
_shortcut_display_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache_key(action: str, context: KeybindingContextName) -> str:
    """Derive a stable cache key from an action + context pair."""
    return f"{action}:{context}"


def _detect_platform() -> str:
    """Return the current OS as a platform name understood by the parser.

    Returns one of ``'macos'``, ``'windows'``, or ``'linux'``.
    """
    system = _platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    return "linux"


def _log_fallback(
    action: str,
    context: KeybindingContextName,
    fallback: str,
    *,
    reason: str = "action_not_found",
) -> None:
    """Log a keybinding-fallback event at most once per action+context pair.

    The dedup guard lives in a module-level ``set`` so the same action pressed
    repeatedly in a non-React context (e.g. a service loop) only emits a
    single analytics event per process lifetime.
    """
    key = _cache_key(action, context)
    if key in _LOGGED_FALLBACKS:
        return
    _LOGGED_FALLBACKS.add(key)
    log_event(
        "tengu_keybinding_fallback_used",
        {
            "action": action,
            "context": context,
            "fallback": fallback,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# Primary API
# ---------------------------------------------------------------------------


def get_shortcut_display(
    action: str,
    context: KeybindingContextName,
    fallback: str,
    *,
    use_cache: bool = True,
) -> str:
    """Return the display text for a configured shortcut.

    Use this in non-React contexts (commands, services, etc.) where React
    hooks are unavailable.

    Args:
        action:   The action name (e.g. ``'app:toggleTranscript'``).
        context:  The keybinding context (e.g. ``'Global'``).
        fallback: Fallback text returned when no binding is found.
        use_cache: When *True* (the default), resolved lookups are cached
                   in-process so subsequent calls for the same pair return
                   immediately without reloading or re-resolving bindings.

    Returns:
        The configured shortcut display text, or *fallback*.

    Example::

        expand = get_shortcut_display('app:toggleTranscript', 'Global', 'ctrl+o')
        # => returns the user-configured binding, or 'ctrl+o' as default
    """
    if use_cache:
        ck = _cache_key(action, context)
        cached = _shortcut_display_cache.get(ck)
        if cached is not None:
            return cached

    # Resolve
    try:
        bindings = load_keybindings_sync()
    except Exception:
        _log_fallback(action, context, fallback, reason="bindings_load_error")
        return fallback

    resolved = get_binding_display_text(action, context, bindings)
    if resolved is None:
        _log_fallback(action, context, fallback)
        result = fallback
    else:
        result = resolved

    if use_cache:
        _shortcut_display_cache[_cache_key(action, context)] = result
    return result


def get_shortcut_display_for_platform(
    action: str,
    context: KeybindingContextName,
    fallback: str,
    *,
    platform_name: Optional[str] = None,
) -> str:
    """Like :func:`get_shortcut_display` but returns a platform-aware string.

    On macOS the display shows ``cmd`` / ``opt``; on Windows/Linux it shows
    ``ctrl`` / ``alt`` (matching the keycaps users actually see on their
    keyboards).

    Args:
        action:        The action name.
        context:       The keybinding context.
        fallback:      Fallback text returned when no binding is found.
        platform_name: Override auto-detection; one of
                       ``'macos'``, ``'windows'``, ``'linux'``.

    Returns:
        Platform-aware shortcut display text, or *fallback*.
    """
    plat = platform_name if platform_name is not None else _detect_platform()

    try:
        bindings = load_keybindings_sync()
    except Exception:
        _log_fallback(action, context, fallback, reason="bindings_load_error")
        return fallback

    # Walk bindings in reverse so user overrides take precedence (same
    # order as TypeScript getBindingDisplayText).
    for b in reversed(bindings):
        if b.action == action and b.context == context:
            return chord_to_display_string(b.chord, plat)

    _log_fallback(action, context, fallback)
    return fallback


def get_shortcut_display_multi_context(
    action: str,
    contexts: list[KeybindingContextName],
    fallback: str,
    *,
    platform_name: Optional[str] = None,
) -> str:
    """Search multiple contexts in order, returning the first match.

    Useful when an action can be bound in different contexts and you want
    the most-visible display (e.g. ``['Chat', 'Global']``).

    Args:
        action:        The action name.
        contexts:      Ordered list of contexts to search.  Earlier entries
                       take precedence.
        fallback:      Fallback text returned when no binding is found in
                       any context.
        platform_name: Override auto-detection; one of
                       ``'macos'``, ``'windows'``, ``'linux'``.

    Returns:
        Platform-aware shortcut display text, or *fallback*.
    """
    plat = platform_name if platform_name is not None else _detect_platform()

    try:
        bindings = load_keybindings_sync()
    except Exception:
        primary = contexts[0] if contexts else "Global"
        _log_fallback(action, primary, fallback, reason="bindings_load_error")
        return fallback

    for ctx in contexts:
        for b in reversed(bindings):
            if b.action == action and b.context == ctx:
                return chord_to_display_string(b.chord, plat)

    primary = contexts[0] if contexts else "Global"
    _log_fallback(action, primary, fallback, reason="action_not_found_multi_context")
    return fallback


def get_shortcut_display_raw(
    action: str,
    context: KeybindingContextName,
) -> tuple[str, Optional[str]]:
    """Return the raw binding display and action string without a fallback.

    Unlike :func:`get_shortcut_display`, this function returns a
    ``(display, action)`` tuple that allows callers to distinguish
    "action not found" from "action found with an empty display".

    Args:
        action:   The action name.
        context:  The keybinding context.

    Returns:
        ``(display_text, resolved_display_text)`` if found, or
        ``("", None)`` if the binding was not found or bindings failed
        to load.
    """
    try:
        bindings = load_keybindings_sync()
        resolved = get_binding_display_text(action, context, bindings)
        if resolved is not None:
            return (resolved, resolved)
    except Exception:
        pass
    return ("", None)


def is_shortcut_configured(
    action: str,
    context: KeybindingContextName,
) -> bool:
    """Return *True* if a binding exists for *action* in *context*.

    Args:
        action:   The action name.
        context:  The keybinding context.

    Returns:
        *True* if the action has a binding for the given context.
    """
    try:
        bindings = load_keybindings_sync()
        return get_binding_display_text(action, context, bindings) is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# State management (testing / lifecycle)
# ---------------------------------------------------------------------------


def reset_shortcut_display_cache() -> None:
    """Clear the in-memory shortcut display cache.

    Call between tests that mutate keybindings so stale cache entries do
    not leak across test cases.
    """
    _shortcut_display_cache.clear()


def reset_shortcut_fallbacks_for_testing() -> None:
    """Reset all internal state for testing.

    Clears both the fallback-dedup set and the display cache, and also
    resets the underlying keybinding loader cache so subsequent calls
    re-read configuration from disk.
    """
    _LOGGED_FALLBACKS.clear()
    reset_shortcut_display_cache()
    reset_keybinding_loader_for_testing()


def _get_logged_fallback_actions() -> list[str]:
    """Return the list of action:context pairs that have logged fallbacks.

    Intended for diagnostic / testing use only.
    """
    return sorted(_LOGGED_FALLBACKS)


def _get_shortcut_display_cache_size() -> int:
    """Return the number of entries in the display cache.

    Intended for diagnostic / testing use only.
    """
    return len(_shortcut_display_cache)


# ---------------------------------------------------------------------------
# Convenience aliases
# ---------------------------------------------------------------------------

# Alias matching the naming conventions used elsewhere in the codebase
# (e.g. stop_hooks.py imports get_shortcut_display by its full name,
# but other callers may prefer the shorter form).
get_shortcut = get_shortcut_display
