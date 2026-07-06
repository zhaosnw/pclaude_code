"""Vim transition table (port of src/vim/transitions.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class TransitionContext:
    cursor: object
    text: str
    set_offset: Callable[[int], None]
    enter_insert: Callable[[int], None]
    on_undo: Callable[[], None] | None = None
    on_dot_repeat: Callable[[], None] | None = None


@dataclass
class TransitionResult:
    next: object | None = None
    execute: Callable[[], None] | None = None


def transition(
    state: object, input_ch: str, ctx: TransitionContext
) -> TransitionResult:
    """Dispatch vim normal-mode input; full logic in TS — extend here."""
    _ = (state, input_ch, ctx)
    return TransitionResult()
