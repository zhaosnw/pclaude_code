"""Match Ink input to parsed keystrokes (port of src/keybindings/match.ts)."""

from __future__ import annotations

from hare.keybindings.ink_key import InkKey
from hare.keybindings.types import ParsedBinding, ParsedKeystroke


def get_key_name(input_ch: str, key: InkKey) -> str | None:
    if key.escape:
        return "escape"
    if key.return_:
        return "enter"
    if key.tab:
        return "tab"
    if key.backspace:
        return "backspace"
    if key.delete:
        return "delete"
    if key.up_arrow:
        return "up"
    if key.down_arrow:
        return "down"
    if key.left_arrow:
        return "left"
    if key.right_arrow:
        return "right"
    if key.page_up:
        return "pageup"
    if key.page_down:
        return "pagedown"
    if key.wheel_up:
        return "wheelup"
    if key.wheel_down:
        return "wheeldown"
    if key.home:
        return "home"
    if key.end:
        return "end"
    if len(input_ch) == 1:
        return input_ch.lower()
    return None


def _modifiers_match(ink: InkKey, target: ParsedKeystroke) -> bool:
    if ink.ctrl != target.ctrl:
        return False
    if ink.shift != target.shift:
        return False
    target_needs_meta = target.alt or target.meta
    if ink.meta != target_needs_meta:
        return False
    if ink.super != target.super:
        return False
    return True


def matches_keystroke(input_ch: str, key: InkKey, target: ParsedKeystroke) -> bool:
    key_name = get_key_name(input_ch, key)
    if key_name != target.key:
        return False
    if key.escape:
        return _modifiers_match(
            InkKey(ctrl=key.ctrl, shift=key.shift, meta=False, super=key.super), target
        )
    return _modifiers_match(key, target)


def matches_binding(input_ch: str, key: InkKey, binding: ParsedBinding) -> bool:
    if len(binding.chord) != 1:
        return False
    ks = binding.chord[0]
    return matches_keystroke(input_ch, key, ks)
