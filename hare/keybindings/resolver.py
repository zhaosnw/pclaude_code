"""Resolve keys to actions (port of src/keybindings/resolver.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from hare.keybindings.ink_key import InkKey
from hare.keybindings.parser import chord_to_string
from hare.keybindings.match import get_key_name, matches_binding
from hare.keybindings.types import KeybindingContextName, ParsedBinding, ParsedKeystroke


@dataclass
class ResolveMatch:
    type: Literal["match"]
    action: str


@dataclass
class ResolveNone:
    type: Literal["none"] = "none"


@dataclass
class ResolveUnbound:
    type: Literal["unbound"] = "unbound"


ResolveResult = Union[ResolveMatch, ResolveNone, ResolveUnbound]


@dataclass
class ChordMatch:
    type: Literal["match"]
    action: str


@dataclass
class ChordNone:
    type: Literal["none"] = "none"


@dataclass
class ChordUnbound:
    type: Literal["unbound"] = "unbound"


@dataclass
class ChordStarted:
    type: Literal["chord_started"]
    pending: list[ParsedKeystroke]


@dataclass
class ChordCancelled:
    type: Literal["chord_cancelled"] = "chord_cancelled"


ChordResolveResult = Union[
    ChordMatch, ChordNone, ChordUnbound, ChordStarted, ChordCancelled
]


def resolve_key(
    input_ch: str,
    key: InkKey,
    active_contexts: list[KeybindingContextName],
    bindings: list[ParsedBinding],
) -> ResolveResult:
    ctx_set = set(active_contexts)
    match: ParsedBinding | None = None
    for binding in bindings:
        if len(binding.chord) != 1:
            continue
        if binding.context not in ctx_set:
            continue
        if matches_binding(input_ch, key, binding):
            match = binding
    if not match:
        return ResolveNone()
    if match.action is None:
        return ResolveUnbound()
    return ResolveMatch(type="match", action=match.action)


def get_binding_display_text(
    action: str,
    context: KeybindingContextName,
    bindings: list[ParsedBinding],
) -> str | None:
    for binding in reversed(bindings):
        if binding.action == action and binding.context == context:
            return chord_to_string(binding.chord)
    return None


def _build_keystroke(input_ch: str, key: InkKey) -> ParsedKeystroke | None:
    from hare.keybindings.types import ParsedKeystroke

    name = get_key_name(input_ch, key)
    if not name:
        return None
    effective_meta = False if key.escape else key.meta
    return ParsedKeystroke(
        key=name,
        ctrl=key.ctrl,
        alt=effective_meta,
        shift=key.shift,
        meta=effective_meta,
        super=key.super,
    )


def keystrokes_equal(a: ParsedKeystroke, b: ParsedKeystroke) -> bool:
    return (
        a.key == b.key
        and a.ctrl == b.ctrl
        and a.shift == b.shift
        and (a.alt or a.meta) == (b.alt or b.meta)
        and a.super == b.super
    )


def _chord_prefix_matches(
    prefix: list[ParsedKeystroke], binding: ParsedBinding
) -> bool:
    if len(prefix) >= len(binding.chord):
        return False
    for i, pk in enumerate(prefix):
        bk = binding.chord[i]
        if not keystrokes_equal(pk, bk):
            return False
    return True


def _chord_exactly_matches(
    chord: list[ParsedKeystroke], binding: ParsedBinding
) -> bool:
    if len(chord) != len(binding.chord):
        return False
    for a, b in zip(chord, binding.chord):
        if not keystrokes_equal(a, b):
            return False
    return True


def resolve_key_with_chord_state(
    input_ch: str,
    key: InkKey,
    active_contexts: list[KeybindingContextName],
    bindings: list[ParsedBinding],
    pending: list[ParsedKeystroke] | None,
) -> ChordResolveResult:
    if key.escape and pending is not None:
        return ChordCancelled()

    current = _build_keystroke(input_ch, key)
    if not current:
        if pending is not None:
            return ChordCancelled()
        return ChordNone()

    test_chord = [*pending, current] if pending else [current]
    ctx_set = set(active_contexts)
    context_bindings = [b for b in bindings if b.context in ctx_set]

    chord_winners: dict[str, str | None] = {}
    for binding in context_bindings:
        if len(binding.chord) > len(test_chord) and _chord_prefix_matches(
            test_chord, binding
        ):
            chord_winners[chord_to_string(binding.chord)] = binding.action

    has_longer = any(a is not None for a in chord_winners.values())
    if has_longer:
        return ChordStarted(type="chord_started", pending=test_chord)

    exact: ParsedBinding | None = None
    for binding in context_bindings:
        if _chord_exactly_matches(test_chord, binding):
            exact = binding
    if exact:
        if exact.action is None:
            return ChordUnbound()
        return ChordMatch(type="match", action=exact.action)

    if pending is not None:
        return ChordCancelled()
    return ChordNone()
