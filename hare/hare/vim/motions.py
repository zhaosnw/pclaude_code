"""Vim motions (port of src/vim/motions.ts).

Pure functions for resolving vim motion key-sequences to Cursor positions.
All functions are side-effect-free — they compute a new Cursor without
mutating any state outside of it.

Motions supported
-----------------
h / l          character left / right
j / k          logical line down / up
gj / gk        display (wrapped) line down / up
w / b / e      forward / backward word  (vim "word")
W / B / E      forward / backward WORD  (non-blank runs)
ge             end of previous vim-word
0 / ^ / $      start / first-non-blank / end of logical line
gg / G         start of first / last line in buffer

Inclusive vs linewise
---------------------
*inclusive* motions include the character at the destination when used
with operators (e.g. ``d$`` deletes through end-of-line).
*linewise* motions cause operators to operate on whole lines.

g-prefix handling
-----------------
Motions like ``ge``, ``gj``, ``gk``, ``gg`` require two keys where
the first is ``g``.  Callers must accumulate the ``g`` prefix and
dispatch to ``resolve_g_prefix_motion`` instead of ``resolve_motion``.
"""

from __future__ import annotations

import logging
from typing import Literal

from hare.vim.cursor import Cursor

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# Single-key motions — dispatched by resolve_motion.
_SINGLE_MOTIONS: frozenset[str] = frozenset(
    {"h", "l", "j", "k", "w", "b", "e", "W", "B", "E", "0", "^", "$", "G"}
)

# Two-key motions where the first character is always "g".
_G_PREFIX_MOTIONS: frozenset[str] = frozenset({"ge", "gj", "gk", "gg"})

# Complete set of all recognised motion strings (both single- and two-key).
ALL_MOTIONS: frozenset[str] = _SINGLE_MOTIONS | _G_PREFIX_MOTIONS

# Motions that move leftwards (backward).
_LEFTWARD_MOTIONS: frozenset[str] = frozenset({"h", "b", "ge", "B", "^", "0"})

# Motions that move rightwards (forward).
_RIGHTWARD_MOTIONS: frozenset[str] = frozenset(
    {"l", "w", "e", "W", "E", "$"}
)

# Motions that move primarily vertically.
_VERTICAL_MOTIONS: frozenset[str] = frozenset({"j", "k", "gj", "gk", "gg", "G"})

# Inclusive motions (the character at the destination is included in the
# operated range *when the cursor moves forward* — backward inclusive
# motions are also included here).
_INCLUSIVE_MOTIONS: frozenset[str] = frozenset({"e", "E", "$", "ge"})

# Linewise motions (operators apply to whole lines).
_LINEWISE_MOTIONS: frozenset[str] = frozenset({"j", "k", "G", "gg"})

# Maximum safe count to prevent runaway loops.
MAX_SAFE_COUNT: int = 10_000


# ── Public API ───────────────────────────────────────────────────────────


def resolve_motion(key: str, cursor: Cursor, count: int = 1) -> Cursor:
    """Apply a single-key motion *count* times and return the new cursor.

    Parameters
    ----------
    key : str
        The motion key (e.g. ``"w"``, ``"j"``, ``"$"``).  Must be one of
        the keys in ``_SINGLE_MOTIONS``.  Two-key ``g``-prefixed motions
        should be dispatched via ``resolve_g_prefix_motion``.
    cursor : Cursor
        Starting cursor position.
    count : int
        Number of times to repeat the motion (minimum 1).

    Returns
    -------
    Cursor
        The new cursor after applying the motion *count* times.

    Raises
    ------
    ValueError
        If *key* is not a recognised single-key motion.
    TypeError
        If *key* is not a string or *cursor* is not a ``Cursor``.

    Notes
    -----
    The motion is applied repeatedly, up to *count* times.  If the
    cursor stops moving (e.g. at buffer boundaries) the iteration
    short-circuits early to save work.
    """
    _validate_key(key, _SINGLE_MOTIONS, "single-key motion")
    _validate_cursor(cursor)
    count = clamp_count(count)

    result = cursor
    for _ in range(count):
        nxt = _apply_single_motion(key, result)
        if nxt.equals(result):
            break
        result = nxt
    return result


def resolve_g_prefix_motion(
    motion: str, cursor: Cursor, count: int = 1
) -> Cursor:
    """Apply a two-key ``g``-prefixed motion such as ``gg`` or ``gj``.

    Parameters
    ----------
    motion : str
        The full two-key motion string (e.g. ``"gg"``, ``"ge"``,
        ``"gj"``, ``"gk"``).
    cursor : Cursor
        Starting cursor position.
    count : int
        Number of times to repeat (minimum 1).

    Returns
    -------
    Cursor
        Resulting cursor.

    Raises
    ------
    ValueError
        If *motion* is not in ``_G_PREFIX_MOTIONS``.
    """
    if not is_g_prefix_motion(motion):
        raise ValueError(
            f"Unknown g-prefix motion: {motion!r}. "
            f"Expected one of {sorted(_G_PREFIX_MOTIONS)}."
        )
    _validate_cursor(cursor)
    count = clamp_count(count)

    result = cursor
    for _ in range(count):
        nxt = _apply_g_prefix_motion(motion, result)
        if nxt.equals(result):
            break
        result = nxt
    return result


# ── Motion helpers ───────────────────────────────────────────────────────


def is_inclusive_motion(key: str) -> bool:
    """Return ``True`` if *key* is an inclusive motion.

    Inclusive motions include the character at the destination when
    used with operators (e.g. ``d$`` deletes through end-of-line).
    """
    return key in _INCLUSIVE_MOTIONS


def is_linewise_motion(key: str) -> bool:
    """Return ``True`` if *key* is a linewise motion.

    Linewise motions cause operators to operate on whole lines
    (e.g. ``dd`` deletes line, ``yy`` yanks line).
    """
    return key in _LINEWISE_MOTIONS


def is_g_prefix_motion(key: str) -> bool:
    """Return ``True`` when *key* is a two-character motion prefixed by ``'g'``."""
    return key in _G_PREFIX_MOTIONS


def is_valid_motion_key(key: str) -> bool:
    """Return ``True`` if *key* is any recognised motion string."""
    return key in ALL_MOTIONS


def motion_direction(key: str) -> Literal["left", "right", "up", "down", "none"]:
    """Return the primary direction of a motion.

    Returns ``"none"`` for unrecognised keys.
    """
    if key in _LEFTWARD_MOTIONS:
        return "left"
    if key in _RIGHTWARD_MOTIONS:
        return "right"
    if key in {"j", "gj", "G"}:
        return "down"
    if key in {"k", "gk", "gg"}:
        return "up"
    return "none"


def clamp_count(count: int) -> int:
    """Clamp *count* to the safe range [1, MAX_SAFE_COUNT].

    Returns 1 for counts <= 0 and MAX_SAFE_COUNT for excessively large
    values.
    """
    if count < 1:
        return 1
    return min(count, MAX_SAFE_COUNT)


def resolve_motion_between(
    motion: str, start: Cursor, end: Cursor, count: int = 1
) -> tuple[Cursor, Cursor]:
    """Apply *motion* from both *start* and *end*, returning the pair.

    This is useful when computing operator ranges where both the
    starting point and an anchor need to be moved by the same motion.
    The two cursors share the same text buffer.

    Only single-key motions are supported.  For g-prefix motions use
    ``resolve_g_prefix_motion`` on each cursor separately.

    Returns
    -------
    tuple[Cursor, Cursor]
        (new_start, new_end) after applying the motion once.

    Raises
    ------
    ValueError
        If *motion* is not a valid single-key motion.
    """
    _ = count  # reserved for future use
    _validate_key(motion, _SINGLE_MOTIONS, "single-key motion")
    _validate_cursor(start)
    _validate_cursor(end)
    return (
        _apply_single_motion(motion, start),
        _apply_single_motion(motion, end),
    )


# ── Internal motion dispatch ─────────────────────────────────────────────


def _apply_single_motion(key: str, cursor: Cursor) -> Cursor:
    """Apply one step of a single-key motion."""
    # Character motions
    if key == "h":
        return cursor.left()
    elif key == "l":
        return cursor.right()

    # Logical line motions
    elif key == "j":
        return cursor.down_logical_line()
    elif key == "k":
        return cursor.up_logical_line()

    # Vim word motions
    elif key == "w":
        return cursor.next_vim_word()
    elif key == "b":
        return cursor.prev_vim_word()
    elif key == "e":
        return cursor.end_of_vim_word()

    # Vim WORD motions (non-blank runs)
    elif key == "W":
        return cursor.next_WORD()
    elif key == "B":
        return cursor.prev_WORD()
    elif key == "E":
        return cursor.end_of_WORD()

    # Line boundaries
    elif key == "0":
        return cursor.start_of_logical_line()
    elif key == "^":
        return cursor.first_non_blank_in_logical_line()
    elif key == "$":
        return cursor.end_of_logical_line()

    # Jump to last line
    elif key == "G":
        return cursor.start_of_last_line()

    # Unknown key — no-op
    return cursor


def _apply_g_prefix_motion(motion: str, cursor: Cursor) -> Cursor:
    """Apply one step of a two-key g-prefix motion.

    The *motion* parameter MUST already be validated as a member of
    ``_G_PREFIX_MOTIONS``.
    """
    if motion == "gj":
        return cursor.down()
    elif motion == "gk":
        return cursor.up()
    elif motion == "gg":
        return cursor.start_of_first_line()
    elif motion == "ge":
        return cursor.end_of_prev_vim_word()
    # Should not be reachable if caller validates first
    return cursor


# ── Validation helpers ───────────────────────────────────────────────────


def _validate_key(key: object, allowed: frozenset[str], label: str) -> None:
    """Raise ``TypeError`` / ``ValueError`` if *key* is not a valid
    motion key inside *allowed*."""
    if not isinstance(key, str):
        raise TypeError(
            f"{label} must be a str, got {type(key).__name__}: {key!r}"
        )
    if not key:
        raise ValueError(f"{label} must be a non-empty string")
    if key not in allowed:
        raise ValueError(
            f"Unknown {label}: {key!r}. "
            f"Expected one of {sorted(allowed)}."
        )


def _validate_cursor(cursor: object) -> None:
    """Raise ``TypeError`` if *cursor* is not a ``Cursor`` instance."""
    if not isinstance(cursor, Cursor):
        raise TypeError(
            f"cursor must be a Cursor, got {type(cursor).__name__}: {cursor!r}"
        )
    # Warn if text is empty — all motions will be no-ops
    if not cursor.text:
        logger.debug("Cursor text is empty; all motions will no-op.")
