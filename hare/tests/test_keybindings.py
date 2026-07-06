"""
Unit tests for hare.keybindings.parser — keystroke/chord parsing.

Port of: src/keybindings/parser.ts behavior verification.
"""

from __future__ import annotations

from hare.keybindings.parser import parse_chord, parse_keystroke


# ---------------------------------------------------------------------------
# parse_keystroke — single key
# ---------------------------------------------------------------------------


def test_parse_simple_key() -> None:
    ks = parse_keystroke("a")
    assert ks.key == "a"
    assert ks.ctrl is False
    assert ks.alt is False
    assert ks.shift is False
    assert ks.meta is False
    assert ks.super is False


def test_parse_ctrl_key() -> None:
    ks = parse_keystroke("ctrl+c")
    assert ks.ctrl is True
    assert ks.key == "c"


def test_parse_control_alias() -> None:
    ks = parse_keystroke("control+x")
    assert ks.ctrl is True
    assert ks.key == "x"


def test_parse_alt_key() -> None:
    ks = parse_keystroke("alt+d")
    assert ks.alt is True
    assert ks.key == "d"


def test_parse_option_alias() -> None:
    ks = parse_keystroke("opt+f")
    assert ks.alt is True
    assert ks.key == "f"


def test_parse_shift_key() -> None:
    ks = parse_keystroke("shift+a")
    assert ks.shift is True
    assert ks.key == "a"


def test_parse_meta_key() -> None:
    ks = parse_keystroke("meta+x")
    assert ks.meta is True
    assert ks.key == "x"


def test_parse_super_key() -> None:
    ks = parse_keystroke("super+s")
    assert ks.super is True
    assert ks.key == "s"


def test_parse_cmd_alias() -> None:
    ks = parse_keystroke("cmd+q")
    assert ks.super is True
    assert ks.key == "q"


def test_parse_command_alias() -> None:
    ks = parse_keystroke("command+w")
    assert ks.super is True
    assert ks.key == "w"


def test_parse_win_alias() -> None:
    ks = parse_keystroke("win+r")
    assert ks.super is True
    assert ks.key == "r"


# ---------------------------------------------------------------------------
# parse_keystroke — special key names
# ---------------------------------------------------------------------------


def test_parse_escape() -> None:
    ks = parse_keystroke("esc")
    assert ks.key == "escape"


def test_parse_return() -> None:
    ks = parse_keystroke("return")
    assert ks.key == "enter"


def test_parse_space() -> None:
    ks = parse_keystroke("space")
    assert ks.key == " "


def test_parse_arrow_up() -> None:
    ks = parse_keystroke("↑")
    assert ks.key == "up"


def test_parse_arrow_down() -> None:
    ks = parse_keystroke("↓")
    assert ks.key == "down"


def test_parse_arrow_left() -> None:
    ks = parse_keystroke("←")
    assert ks.key == "left"


def test_parse_arrow_right() -> None:
    ks = parse_keystroke("→")
    assert ks.key == "right"


# ---------------------------------------------------------------------------
# parse_keystroke — combined modifiers
# ---------------------------------------------------------------------------


def test_parse_ctrl_shift_key() -> None:
    ks = parse_keystroke("ctrl+shift+a")
    assert ks.ctrl is True
    assert ks.shift is True
    assert ks.key == "a"


def test_parse_ctrl_alt_shift_key() -> None:
    ks = parse_keystroke("ctrl+alt+shift+d")
    assert ks.ctrl is True
    assert ks.alt is True
    assert ks.shift is True
    assert ks.key == "d"


def test_parse_cmd_shift_key() -> None:
    ks = parse_keystroke("cmd+shift+p")
    assert ks.super is True
    assert ks.shift is True
    assert ks.key == "p"


# ---------------------------------------------------------------------------
# parse_chord
# ---------------------------------------------------------------------------


def test_parse_chord_single_key() -> None:
    chord = parse_chord("a")
    assert len(chord) == 1
    assert chord[0].key == "a"


def test_parse_chord_space() -> None:
    chord = parse_chord(" ")
    assert len(chord) == 1
    assert chord[0].key == " "


def test_parse_chord_multiple_keys() -> None:
    chord = parse_chord("ctrl+k ctrl+s")
    assert len(chord) == 2
    assert chord[0].ctrl is True
    assert chord[0].key == "k"
    assert chord[1].ctrl is True
    assert chord[1].key == "s"


def test_parse_chord_with_special_keys() -> None:
    chord = parse_chord("ctrl+shift+↑")
    assert len(chord) == 1
    assert chord[0].ctrl is True
    assert chord[0].shift is True
    assert chord[0].key == "up"
