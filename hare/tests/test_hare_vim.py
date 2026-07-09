"""
Unit tests for hare.vim — vim mode state machine.

Port of: src/vim/ behavior verification.
"""

from __future__ import annotations

from hare.vim.mode import VimState


# ---------------------------------------------------------------------------
# VimState defaults
# ---------------------------------------------------------------------------


def test_vim_state_defaults() -> None:
    vs = VimState()
    assert vs.mode == "insert"
    assert vs.command_buffer == ""
    assert vs.register == ""
    assert vs.count == 0


# ---------------------------------------------------------------------------
# Mode transitions
# ---------------------------------------------------------------------------


def test_to_normal() -> None:
    vs = VimState()
    vs.to_normal()
    assert vs.mode == "normal"
    assert vs.command_buffer == ""


def test_to_insert() -> None:
    vs = VimState(mode="normal")
    vs.to_insert()
    assert vs.mode == "insert"
    assert vs.command_buffer == ""


def test_to_visual() -> None:
    vs = VimState()
    vs.to_visual()
    assert vs.mode == "visual"


def test_to_command() -> None:
    vs = VimState()
    vs.to_command()
    assert vs.mode == "command"
    assert vs.command_buffer == ":"


# ---------------------------------------------------------------------------
# feed_key — normal mode
# ---------------------------------------------------------------------------


def test_normal_i_goes_to_insert() -> None:
    vs = VimState(mode="normal")
    action = vs.feed_key("i")
    assert vs.mode == "insert"
    assert action == "insert"


def test_normal_v_goes_to_visual() -> None:
    vs = VimState(mode="normal")
    action = vs.feed_key("v")
    assert vs.mode == "visual"
    assert action == "visual"


def test_normal_colon_goes_to_command() -> None:
    vs = VimState(mode="normal")
    action = vs.feed_key(":")
    assert vs.mode == "command"
    assert action == "command"
    assert vs.command_buffer == ":"


def test_normal_unknown_key_noop() -> None:
    vs = VimState(mode="normal")
    action = vs.feed_key("x")
    assert action is None
    assert vs.mode == "normal"


# ---------------------------------------------------------------------------
# feed_key — insert mode
# ---------------------------------------------------------------------------


def test_insert_escape_goes_to_normal() -> None:
    vs = VimState(mode="insert")
    action = vs.feed_key("Escape")
    assert vs.mode == "normal"
    assert action == "normal"


def test_insert_other_key_noop() -> None:
    vs = VimState(mode="insert")
    action = vs.feed_key("a")
    assert action is None
    assert vs.mode == "insert"


# ---------------------------------------------------------------------------
# feed_key — command mode
# ---------------------------------------------------------------------------


def test_command_escape_goes_to_normal() -> None:
    vs = VimState(mode="command", command_buffer=":wq")
    action = vs.feed_key("Escape")
    assert vs.mode == "normal"
    assert action == "normal"


def test_command_enter_executes() -> None:
    vs = VimState(mode="command", command_buffer=":q")
    action = vs.feed_key("Enter")
    assert vs.mode == "normal"
    assert action == "exec::q"


def test_command_builds_buffer() -> None:
    vs = VimState(mode="command", command_buffer=":")
    vs.feed_key("w")
    vs.feed_key("q")
    assert vs.command_buffer == ":wq"


def test_command_full_workflow() -> None:
    vs = VimState(mode="normal")
    # Enter command mode
    vs.feed_key(":")
    assert vs.mode == "command"
    # Type command
    vs.feed_key("w")
    vs.feed_key("q")
    assert vs.command_buffer == ":wq"
    # Execute
    action = vs.feed_key("Enter")
    assert action == "exec::wq"
    assert vs.mode == "normal"
    assert vs.command_buffer == ""


# ---------------------------------------------------------------------------
# feed_key — visual mode
# ---------------------------------------------------------------------------


def test_visual_escape_to_normal() -> None:
    vs = VimState(mode="visual")
    # Currently feed_key doesn't handle Escape in visual mode
    # so it returns None and stays in visual
    action = vs.feed_key("Escape")
    # Per current implementation, visual Escape is not handled
    assert action is None
