"""Port of: src/utils/deepLink/terminalPreference.ts"""

from __future__ import annotations
import os
import json
from typing import Literal

TerminalPreference = Literal["default", "iterm2", "terminal", "wezterm", "alacritty"]


def get_terminal_preference() -> TerminalPreference:
    config_path = os.path.join(os.path.expanduser("~"), ".hare", "config.json")
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
        return data.get("terminalPreference", "default")
    except Exception:
        return "default"
