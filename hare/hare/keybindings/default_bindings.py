"""
Default keybindings (port of src/keybindings/defaultBindings.ts).

The upstream file is large and feature-flag driven; this module keeps the
same structure with a representative subset. Expand as needed for parity.
"""

from __future__ import annotations

import os
from hare.keybindings.types import KeybindingBlock


def _image_paste_key() -> str:
    return "alt+v" if os.name == "nt" else "ctrl+v"


def _mode_cycle_key() -> str:
    # TS uses VT mode detection; portable default:
    return "shift+tab"


DEFAULT_BINDINGS: list[KeybindingBlock] = [
    KeybindingBlock(
        context="Global",
        bindings={
            "ctrl+c": "app:interrupt",
            "ctrl+d": "app:exit",
            "ctrl+l": "app:redraw",
            "ctrl+t": "app:toggleTodos",
            "ctrl+o": "app:toggleTranscript",
            "ctrl+shift+o": "app:toggleTeammatePreview",
            "ctrl+r": "history:search",
        },
    ),
    KeybindingBlock(
        context="Chat",
        bindings={
            "escape": "chat:cancel",
            "ctrl+x ctrl+k": "chat:killAgents",
            _mode_cycle_key(): "chat:cycleMode",
            "meta+p": "chat:modelPicker",
            "meta+o": "chat:fastMode",
            "meta+t": "chat:thinkingToggle",
            "enter": "chat:submit",
            "up": "history:previous",
            "down": "history:next",
            "ctrl+_": "chat:undo",
            "ctrl+shift+-": "chat:undo",
            "ctrl+x ctrl+e": "chat:externalEditor",
            "ctrl+g": "chat:externalEditor",
            "ctrl+s": "chat:stash",
            _image_paste_key(): "chat:imagePaste",
        },
    ),
    KeybindingBlock(
        context="Autocomplete",
        bindings={
            "tab": "autocomplete:accept",
            "escape": "autocomplete:dismiss",
            "up": "autocomplete:previous",
            "down": "autocomplete:next",
        },
    ),
    KeybindingBlock(
        context="Confirmation",
        bindings={
            "y": "confirm:yes",
            "n": "confirm:no",
            "enter": "confirm:yes",
            "escape": "confirm:no",
        },
    ),
]
