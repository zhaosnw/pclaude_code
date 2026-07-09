"""
Tests for keybindings/match.py — keystroke/binding matching logic.
"""

from __future__ import annotations

from hare.keybindings.ink_key import InkKey
from hare.keybindings.match import (
    get_key_name,
    matches_binding,
    matches_keystroke,
)
from hare.keybindings.types import ParsedBinding, ParsedKeystroke


def _keystroke(
    key: str,
    ctrl: bool = False,
    shift: bool = False,
    meta: bool = False,
    alt: bool = False,
    super: bool = False,
) -> ParsedKeystroke:
    return ParsedKeystroke(
        key=key,
        ctrl=ctrl,
        shift=shift,
        meta=meta,
        alt=alt,
        super=super,
    )


def _binding(chord: list[ParsedKeystroke]) -> ParsedBinding:
    return ParsedBinding(chord=chord, action=None, context="Global")


# ---------------------------------------------------------------------------
# get_key_name tests
# ---------------------------------------------------------------------------


class TestGetKeyName:
    def test_regular_character(self) -> None:
        key = InkKey()
        assert get_key_name("a", key) == "a"

    def test_uppercase_character_lowered(self) -> None:
        key = InkKey()
        assert get_key_name("Z", key) == "z"

    def test_escape(self) -> None:
        key = InkKey(escape=True)
        assert get_key_name("", key) == "escape"

    def test_enter(self) -> None:
        key = InkKey(return_=True)
        assert get_key_name("", key) == "enter"

    def test_tab(self) -> None:
        key = InkKey(tab=True)
        assert get_key_name("", key) == "tab"

    def test_backspace(self) -> None:
        key = InkKey(backspace=True)
        assert get_key_name("", key) == "backspace"

    def test_delete(self) -> None:
        key = InkKey(delete=True)
        assert get_key_name("", key) == "delete"

    def test_up_arrow(self) -> None:
        key = InkKey(up_arrow=True)
        assert get_key_name("", key) == "up"

    def test_down_arrow(self) -> None:
        key = InkKey(down_arrow=True)
        assert get_key_name("", key) == "down"

    def test_left_arrow(self) -> None:
        key = InkKey(left_arrow=True)
        assert get_key_name("", key) == "left"

    def test_right_arrow(self) -> None:
        key = InkKey(right_arrow=True)
        assert get_key_name("", key) == "right"

    def test_page_up(self) -> None:
        key = InkKey(page_up=True)
        assert get_key_name("", key) == "pageup"

    def test_page_down(self) -> None:
        key = InkKey(page_down=True)
        assert get_key_name("", key) == "pagedown"

    def test_home(self) -> None:
        key = InkKey(home=True)
        assert get_key_name("", key) == "home"

    def test_end(self) -> None:
        key = InkKey(end=True)
        assert get_key_name("", key) == "end"

    def test_wheel_up(self) -> None:
        key = InkKey(wheel_up=True)
        assert get_key_name("", key) == "wheelup"

    def test_wheel_down(self) -> None:
        key = InkKey(wheel_down=True)
        assert get_key_name("", key) == "wheeldown"

    def test_unknown_multi_char_input(self) -> None:
        key = InkKey()
        assert get_key_name("F1", key) is None


# ---------------------------------------------------------------------------
# matches_keystroke tests
# ---------------------------------------------------------------------------


class TestMatchesKeystroke:
    def test_exact_match_no_modifiers(self) -> None:
        key = InkKey()
        target = _keystroke("a")
        assert matches_keystroke("a", key, target) is True

    def test_key_name_mismatch(self) -> None:
        key = InkKey()
        target = _keystroke("b")
        assert matches_keystroke("a", key, target) is False

    def test_ctrl_match(self) -> None:
        key = InkKey(ctrl=True)
        target = _keystroke("c", ctrl=True)
        assert matches_keystroke("c", key, target) is True

    def test_ctrl_mismatch(self) -> None:
        key = InkKey(ctrl=False)
        target = _keystroke("c", ctrl=True)
        assert matches_keystroke("c", key, target) is False

    def test_shift_match(self) -> None:
        # get_key_name lowers the character, so match against lowered key
        key = InkKey(shift=True)
        target = _keystroke("a", shift=True)
        assert matches_keystroke("A", key, target) is True

    def test_escape_with_modifiers(self) -> None:
        key = InkKey(escape=True, ctrl=True)
        target = _keystroke("escape", ctrl=True)
        assert matches_keystroke("", key, target) is True

    def test_escape_ignores_meta(self) -> None:
        # Escape key has special meta-handling — meta is ignored in modifier check
        key = InkKey(escape=True, meta=True, ctrl=True)
        target = _keystroke("escape", ctrl=True)
        assert matches_keystroke("", key, target) is True

    def test_meta_alt_combined(self) -> None:
        # alt and meta are OR'd together in the target check
        key = InkKey(meta=True)
        target = _keystroke("x", alt=True)
        assert matches_keystroke("x", key, target) is True

    def test_super_modifier(self) -> None:
        key = InkKey(super=True)
        target = _keystroke("s", super=True)
        assert matches_keystroke("s", key, target) is True

    def test_super_mismatch(self) -> None:
        key = InkKey(super=False)
        target = _keystroke("s", super=True)
        assert matches_keystroke("s", key, target) is False


# ---------------------------------------------------------------------------
# matches_binding tests
# ---------------------------------------------------------------------------


class TestMatchesBinding:
    def test_single_chord_match(self) -> None:
        key = InkKey()
        binding = _binding([_keystroke("x")])
        assert matches_binding("x", key, binding) is True

    def test_single_chord_mismatch(self) -> None:
        key = InkKey()
        binding = _binding([_keystroke("b")])
        assert matches_binding("a", key, binding) is False

    def test_multi_chord_rejected(self) -> None:
        key = InkKey()
        binding = _binding([_keystroke("a"), _keystroke("b")])
        assert matches_binding("a", key, binding) is False

    def test_empty_chord_rejected(self) -> None:
        key = InkKey()
        binding = ParsedBinding(chord=[], action=None, context="Global")
        assert matches_binding("a", key, binding) is False

    def test_ctrl_shortcut_match(self) -> None:
        key = InkKey(ctrl=True)
        binding = _binding([_keystroke("d", ctrl=True)])
        assert matches_binding("d", key, binding) is True
