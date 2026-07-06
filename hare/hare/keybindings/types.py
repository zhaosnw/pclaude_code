"""Keybinding structural types (port of inferred src/keybindings/types.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Chord = list["ParsedKeystroke"]

KeybindingContextName = Literal[
    "Global",
    "Chat",
    "Autocomplete",
    "Confirmation",
    "Help",
    "Transcript",
    "HistorySearch",
    "Task",
    "ThemePicker",
    "Settings",
    "Tabs",
    "Attachments",
    "Footer",
    "MessageSelector",
    "DiffDialog",
    "ModelPicker",
    "Select",
    "Plugin",
    "Scroll",
    "MessageActions",
]  # keep aligned with schema.KEYBINDING_CONTEXTS + TS-only contexts


@dataclass
class ParsedKeystroke:
    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False
    super: bool = False


@dataclass
class ParsedBinding:
    chord: Chord
    action: str | None
    context: KeybindingContextName


@dataclass
class KeybindingBlock:
    context: KeybindingContextName
    bindings: dict[str, str | None]
