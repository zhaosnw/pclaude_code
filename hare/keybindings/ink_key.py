"""Ink `Key` stand-in for terminal input (port subset for match/resolver)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InkKey:
    ctrl: bool = False
    shift: bool = False
    meta: bool = False
    super: bool = False
    escape: bool = False
    return_: bool = False
    tab: bool = False
    backspace: bool = False
    delete: bool = False
    up_arrow: bool = False
    down_arrow: bool = False
    left_arrow: bool = False
    right_arrow: bool = False
    page_up: bool = False
    page_down: bool = False
    wheel_up: bool = False
    wheel_down: bool = False
    home: bool = False
    end: bool = False
