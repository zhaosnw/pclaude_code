"""Reserved / non-rebindable shortcuts (port of src/keybindings/reservedShortcuts.ts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal


# ---------------------------------------------------------------------------
# Key aliases for consistent normalization
# ---------------------------------------------------------------------------

KEY_ALIASES: dict[str, str] = {
    "escape": "esc",
    "esc": "esc",
    "return": "enter",
    "enter": "enter",
    "space": " ",
    " ": " ",
    "up": "up",
    "↑": "up",
    "down": "down",
    "↓": "down",
    "left": "left",
    "←": "left",
    "right": "right",
    "→": "right",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "tab": "tab",
    "home": "home",
    "end": "end",
    "insert": "insert",
    "ins": "insert",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
    "capslock": "capslock",
    "numlock": "numlock",
    "printscreen": "printscreen",
    "prtsc": "printscreen",
    "scrolllock": "scrolllock",
}

# ---------------------------------------------------------------------------
# Modifier canonicalization mapping
# ---------------------------------------------------------------------------

_MODIFIER_CANONICAL: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "opt": "alt",
    "option": "alt",
    "meta": "meta",
    "cmd": "cmd",
    "command": "cmd",
    "super": "super",
    "win": "super",
    "windows": "super",
    "shift": "shift",
}


@dataclass
class ReservedShortcut:
    key: str
    reason: str
    severity: Literal["error", "warning"]


# ---------------------------------------------------------------------------
# Platform-specific reserved shortcut lists
# ---------------------------------------------------------------------------

NON_REBINDABLE: list[ReservedShortcut] = [
    ReservedShortcut(
        key="ctrl+c",
        reason="Cannot be rebound - used for interrupt/exit (hardcoded)",
        severity="error",
    ),
    ReservedShortcut(
        key="ctrl+d",
        reason="Cannot be rebound - used for exit (hardcoded)",
        severity="error",
    ),
    ReservedShortcut(
        key="ctrl+m",
        reason="Cannot be rebound - identical to Enter in terminals (both send CR)",
        severity="error",
    ),
]

TERMINAL_RESERVED: list[ReservedShortcut] = [
    ReservedShortcut(
        key="ctrl+z",
        reason="Unix process suspend (SIGTSTP)",
        severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+\\",
        reason="Terminal quit signal (SIGQUIT)",
        severity="error",
    ),
    ReservedShortcut(
        key="ctrl+s",
        reason="Terminal flow control - freezes output (XOFF). May need `stty -ixon` to use.",
        severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+q",
        reason="Terminal flow control - resumes output (XON). May need `stty -ixon` to use.",
        severity="warning",
    ),
]

MACOS_RESERVED: list[ReservedShortcut] = [
    ReservedShortcut(key="cmd+c", reason="macOS system copy", severity="error"),
    ReservedShortcut(key="cmd+v", reason="macOS system paste", severity="error"),
    ReservedShortcut(key="cmd+x", reason="macOS system cut", severity="error"),
    ReservedShortcut(key="cmd+q", reason="macOS quit application", severity="error"),
    ReservedShortcut(key="cmd+w", reason="macOS close window/tab", severity="error"),
    ReservedShortcut(key="cmd+tab", reason="macOS app switcher", severity="error"),
    ReservedShortcut(key="cmd+space", reason="macOS Spotlight", severity="error"),
    ReservedShortcut(key="cmd+h", reason="macOS hide application", severity="warning"),
    ReservedShortcut(key="cmd+m", reason="macOS minimize window", severity="warning"),
    ReservedShortcut(key="cmd+shift+q", reason="macOS log out", severity="error"),
    ReservedShortcut(
        key="cmd+option+esc", reason="macOS Force Quit dialog", severity="error"
    ),
    ReservedShortcut(
        key="ctrl+left", reason="macOS Mission Control - move left a space", severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+right", reason="macOS Mission Control - move right a space", severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+up", reason="macOS Mission Control", severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+down", reason="macOS App Expose", severity="warning",
    ),
]

WINDOWS_RESERVED: list[ReservedShortcut] = [
    ReservedShortcut(
        key="alt+f4", reason="Windows close window / shut down dialog", severity="error"
    ),
    ReservedShortcut(
        key="alt+tab", reason="Windows app switcher", severity="error"
    ),
    ReservedShortcut(
        key="alt+space", reason="Windows window menu", severity="warning"
    ),
    ReservedShortcut(
        key="win+l", reason="Windows lock screen", severity="error"
    ),
    ReservedShortcut(
        key="win+r", reason="Windows Run dialog", severity="warning"
    ),
    ReservedShortcut(
        key="win+d", reason="Windows show desktop", severity="warning"
    ),
    ReservedShortcut(
        key="win+e", reason="Windows File Explorer", severity="warning"
    ),
    ReservedShortcut(
        key="win+tab", reason="Windows Task View", severity="warning"
    ),
    ReservedShortcut(
        key="ctrl+alt+delete",
        reason="Windows security screen (hardcoded)",
        severity="error",
    ),
    ReservedShortcut(
        key="ctrl+shift+escape",
        reason="Windows Task Manager",
        severity="warning",
    ),
    ReservedShortcut(
        key="win+m", reason="Windows minimize all windows", severity="warning"
    ),
    ReservedShortcut(
        key="win+shift+s", reason="Windows Snipping Tool", severity="warning"
    ),
    ReservedShortcut(
        key="win+v", reason="Windows clipboard history", severity="warning"
    ),
]

LINUX_RESERVED: list[ReservedShortcut] = [
    ReservedShortcut(
        key="alt+f4",
        reason="Linux desktop environment - close window",
        severity="warning",
    ),
    ReservedShortcut(
        key="alt+f2",
        reason="Linux desktop environment - run command dialog",
        severity="warning",
    ),
    ReservedShortcut(
        key="alt+tab",
        reason="Linux desktop environment - app switcher",
        severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+alt+delete",
        reason="Linux desktop environment - system dialog",
        severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+alt+t",
        reason="Linux desktop environment - open terminal",
        severity="warning",
    ),
    ReservedShortcut(
        key="ctrl+alt+l",
        reason="Linux desktop environment - lock screen",
        severity="warning",
    ),
    ReservedShortcut(
        key="alt+space",
        reason="Linux desktop environment - window menu (KDE/GNOME)",
        severity="warning",
    ),
]


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def get_platform() -> str:
    """Stub: replace with hare.utils.platform.get_platform when wired."""
    import sys

    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"


# ---------------------------------------------------------------------------
# Reserved shortcut aggregation
# ---------------------------------------------------------------------------


def get_reserved_shortcuts() -> list[ReservedShortcut]:
    reserved = list(NON_REBINDABLE) + list(TERMINAL_RESERVED)
    platform = get_platform()
    if platform == "macos":
        reserved.extend(MACOS_RESERVED)
    elif platform == "windows":
        reserved.extend(WINDOWS_RESERVED)
    else:
        reserved.extend(LINUX_RESERVED)
    return reserved


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_key_for_comparison(key: str) -> str:
    """Normalize a keystroke or chord for comparison.

    Handles chord sequences (e.g. ``"ctrl+k ctrl+b"``) by normalizing each
    keystroke independently and re-joining.  Empty or whitespace-only input
    returns an empty string.
    """
    if not isinstance(key, str) or not key.strip():
        return ""
    return " ".join(normalize_step(s) for s in key.strip().split() if s.strip())


def normalize_step(step: str) -> str:
    """Normalize a single keystroke (modifier+key combination).

    Canonicalizes modifier names (``control`` → ``ctrl``, ``option``/``opt``
    → ``alt``, ``command``/``cmd`` → ``cmd``, ``win``/``windows`` →
    ``super``) and key names via :data:`KEY_ALIASES`.  Modifiers are sorted
    alphabetically so ``"shift+ctrl+a"`` and ``"ctrl+shift+a"`` compare
    equal.  Raises :class:`ValueError` when the step is empty or contains no
    recognisable modifiers or key.
    """
    if not isinstance(step, str) or not step.strip():
        return ""

    parts = step.split("+")
    modifiers: list[str] = []
    main_key: str | None = None

    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        lower = stripped.lower()

        if lower in _MODIFIER_CANONICAL:
            canonical = _MODIFIER_CANONICAL[lower]
            if canonical not in modifiers:
                modifiers.append(canonical)
        else:
            # Canonicalize key name to a stable form
            main_key = KEY_ALIASES.get(lower, lower)

    # If no main key was found (e.g. the input was all modifiers such as
    # "ctrl+shift"), treat the last modifier-like segment as the key.
    if main_key is None and modifiers:
        main_key = modifiers.pop()

    modifiers.sort()
    key_part = main_key if main_key is not None else ""
    if modifiers:
        return "+".join([*modifiers, key_part])
    return key_part


# ---------------------------------------------------------------------------
# Internal lookup cache
# ---------------------------------------------------------------------------

@lru_cache(maxsize=256)
def _build_reserved_lookup() -> dict[str, ReservedShortcut]:
    """Build a dict mapping normalized key → first-matching ReservedShortcut.

    When multiple entries normalise to the same key, error-level entries
    take precedence over warnings.
    """
    lookup: dict[str, ReservedShortcut] = {}
    for rs in get_reserved_shortcuts():
        norm = normalize_key_for_comparison(rs.key)
        if not norm:
            continue
        existing = lookup.get(norm)
        if existing is None or (
            existing.severity == "warning" and rs.severity == "error"
        ):
            lookup[norm] = rs
    return lookup


# ---------------------------------------------------------------------------
# Public lookup / validation API
# ---------------------------------------------------------------------------


def is_key_reserved(key: str) -> bool:
    """Return *True* if *key* (or any keystroke in a chord) is reserved."""
    return get_reserved_info(key) is not None


def get_reserved_info(key: str) -> ReservedShortcut | None:
    """Return the :class:`ReservedShortcut` matching *key*, or *None*.

    For chord bindings (e.g. ``"ctrl+k ctrl+b"``) each individual keystroke
    is checked.  The first match is returned, preferring errors over warnings.
    """
    if not isinstance(key, str) or not key.strip():
        return None

    lookup = _build_reserved_lookup()
    normalized = normalize_key_for_comparison(key)
    if not normalized:
        return None

    # Single keystroke – direct lookup.
    if " " not in normalized:
        return lookup.get(normalized)

    # Chord – check each keystroke.  Errors beat warnings.
    found_error: ReservedShortcut | None = None
    found_warning: ReservedShortcut | None = None
    for part in normalized.split(" "):
        part = part.strip()
        if not part:
            continue
        match = lookup.get(part)
        if match is None:
            continue
        if match.severity == "error":
            found_error = match
        elif found_warning is None:
            found_warning = match

    if found_error is not None:
        return found_error
    return found_warning


def is_rebindable(key: str) -> bool:
    """Return *True* if *key* can be rebound.

    Warning-level reserved shortcuts are still considered rebindable
    (the user can override them at their own risk).
    """
    info = get_reserved_info(key)
    if info is None:
        return True
    return info.severity != "error"


def get_all_matching_reserved(key: str) -> list[ReservedShortcut]:
    """Return **all** :class:`ReservedShortcut` entries that match *key*.

    For chord bindings each individual keystroke is checked against the
    full reserved list.  Results are sorted so that errors come before
    warnings.
    """
    if not isinstance(key, str) or not key.strip():
        return []

    normalized = normalize_key_for_comparison(key)
    if not normalized:
        return []

    all_reserved = get_reserved_shortcuts()
    parts = set(normalized.split(" "))
    results: list[ReservedShortcut] = []

    for rs in all_reserved:
        rs_norm = normalize_key_for_comparison(rs.key)
        if not rs_norm:
            continue
        if rs_norm in parts or rs_norm == normalized:
            if rs not in results:
                results.append(rs)

    # Sort errors first, then by key name for determinism
    results.sort(key=lambda r: (0 if r.severity == "error" else 1, r.key))
    return results


# ---------------------------------------------------------------------------
# Structured validation result
# ---------------------------------------------------------------------------


@dataclass
class ReservedCheckResult:
    """Result of checking a keybinding against reserved shortcut lists."""

    key: str
    is_reserved: bool
    severity: Literal["error", "warning"] | None = None
    reason: str | None = None
    matches: list[ReservedShortcut] = field(default_factory=list)


def validate_keybinding(key: str) -> ReservedCheckResult:
    """Validate a single keybinding string against all reserved shortcut lists.

    Returns a structured :class:`ReservedCheckResult` detailing any conflicts.
    """
    if not isinstance(key, str) or not key.strip():
        return ReservedCheckResult(key=key, is_reserved=False)

    matches = get_all_matching_reserved(key)
    if not matches:
        return ReservedCheckResult(key=key, is_reserved=False)

    # Determine overall severity – worst wins.
    severity: Literal["error", "warning"] = "warning"
    reason_parts: list[str] = []
    for m in matches:
        if m.severity == "error":
            severity = "error"
        reason_parts.append(m.reason)

    return ReservedCheckResult(
        key=key,
        is_reserved=True,
        severity=severity,
        reason="; ".join(reason_parts),
        matches=matches,
    )


def validate_keybindings(keys: list[str]) -> list[ReservedCheckResult]:
    """Bulk-validate a list of keybinding strings.

    Returns one :class:`ReservedCheckResult` per input key, in input order.
    """
    return [validate_keybinding(k) for k in keys]


def find_reserved_in_bindings(
    bindings: dict[str, object],
) -> list[tuple[str, ReservedShortcut]]:
    """Scan *bindings* and return ``(key, ReservedShortcut)`` pairs for any
    reserved keys found."""
    results: list[tuple[str, ReservedShortcut]] = []
    for key in bindings:
        info = get_reserved_info(key)
        if info is not None:
            results.append((key, info))
    return results


# ---------------------------------------------------------------------------
# User-facing message formatting
# ---------------------------------------------------------------------------


def format_reserved_message(key: str) -> str | None:
    """Format a user-friendly single-line message about why *key* is reserved.

    Returns *None* when *key* is **not** reserved.
    """
    info = get_reserved_info(key)
    if info is None:
        return None
    icon = "✗" if info.severity == "error" else "⚠"
    return f'{icon} "{key}" is {info.severity}-level reserved: {info.reason}'


def format_all_reserved_messages(key: str) -> list[str]:
    """Format user-friendly messages for **all** reserved matches on *key*."""
    matches = get_all_matching_reserved(key)
    if not matches:
        return []
    lines: list[str] = []
    for m in matches:
        icon = "✗" if m.severity == "error" else "⚠"
        lines.append(f'{icon} "{m.key}" ({m.severity}): {m.reason}')
    return lines


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def get_reserved_keys_for_display() -> dict[str, list[str]]:
    """Return reserved keys grouped by category for display purposes.

    Keys are category names (``"Non-rebindable"``, ``"Terminal"``,
    ``"macOS"`` / ``"Windows"`` / ``"Linux Desktop"``) and values are lists
    of key strings in canonical form.
    """
    categories: dict[str, list[ReservedShortcut]] = {
        "Non-rebindable": NON_REBINDABLE,
        "Terminal": TERMINAL_RESERVED,
    }
    platform = get_platform()
    if platform == "macos":
        categories["macOS"] = MACOS_RESERVED
    elif platform == "windows":
        categories["Windows"] = WINDOWS_RESERVED
    else:
        categories["Linux Desktop"] = LINUX_RESERVED

    result: dict[str, list[str]] = {}
    for category, shortcuts in categories.items():
        result[category] = [rs.key for rs in shortcuts]
    return result


# ---------------------------------------------------------------------------
# Cache management (testing support)
# ---------------------------------------------------------------------------


def clear_reserved_cache() -> None:
    """Clear internal caches so subsequent calls recompute from scratch.

    Useful when the platform changes during tests or when reserved shortcut
    lists are mutated at runtime.
    """
    _build_reserved_lookup.cache_clear()
