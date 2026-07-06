"""Vim state machine types (port of src/vim/types.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

Operator = Literal["delete", "change", "yank"]
FindType = Literal["f", "F", "t", "T"]
TextObjScope = Literal["inner", "around"]

OPERATORS: dict[str, Operator] = {"d": "delete", "c": "change", "y": "yank"}


def is_operator_key(key: str) -> bool:
    return key in OPERATORS


SIMPLE_MOTIONS = frozenset(
    {"h", "l", "j", "k", "w", "b", "e", "W", "B", "E", "0", "^", "$"}
)
FIND_KEYS = frozenset({"f", "F", "t", "T"})
TEXT_OBJ_SCOPES: dict[str, TextObjScope] = {"i": "inner", "a": "around"}
TEXT_OBJ_TYPES = frozenset(
    {
        "w",
        "W",
        '"',
        "'",
        "`",
        "(",
        ")",
        "b",
        "[",
        "]",
        "{",
        "}",
        "B",
        "<",
        ">",
    }
)
MAX_VIM_COUNT = 10_000


class CommandState(TypedDict, total=False):
    type: str


@dataclass
class PersistentState:
    last_change: object | None = None
    last_find: tuple[FindType, str] | None = None
    register: str = ""
    register_is_linewise: bool = False


def create_initial_persistent_state() -> PersistentState:
    return PersistentState()
