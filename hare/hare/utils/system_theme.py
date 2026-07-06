"""
Terminal dark/light hints for the ``auto`` theme setting.

Port of: src/utils/systemTheme.ts
"""

from __future__ import annotations

import os
import re
from typing import Literal

SystemTheme = Literal["dark", "light"]
# ThemeSetting / ThemeName: see theme.py

_cached_system_theme: SystemTheme | None = None


def get_system_theme_name() -> SystemTheme:
    global _cached_system_theme
    if _cached_system_theme is None:
        _cached_system_theme = detect_from_color_fg_bg() or "dark"
    return _cached_system_theme


def set_cached_system_theme(theme: SystemTheme) -> None:
    global _cached_system_theme
    _cached_system_theme = theme


def resolve_theme_setting(setting: str) -> str:
    if setting == "auto":
        return get_system_theme_name()
    return setting


def theme_from_osc_color(data: str) -> SystemTheme | None:
    rgb = _parse_osc_rgb(data)
    if rgb is None:
        return None
    r, g, b = rgb
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "light" if luminance > 0.5 else "dark"


def _hex_component(hex_str: str) -> float:
    max_val = 16 ** len(hex_str) - 1
    return int(hex_str, 16) / max_val


def _parse_osc_rgb(data: str) -> tuple[float, float, float] | None:
    m = re.match(
        r"^rgba?:([0-9a-f]{1,4})/([0-9a-f]{1,4})/([0-9a-f]{1,4})",
        data,
        re.I,
    )
    if m:
        return (
            _hex_component(m.group(1)),
            _hex_component(m.group(2)),
            _hex_component(m.group(3)),
        )
    hm = re.match(r"^#([0-9a-f]+)$", data, re.I)
    if hm:
        hex_body = hm.group(1)
        n = len(hex_body) // 3
        if len(hex_body) % 3 == 0:
            return (
                _hex_component(hex_body[0:n]),
                _hex_component(hex_body[n : 2 * n]),
                _hex_component(hex_body[2 * n : 3 * n]),
            )
    return None


def detect_from_color_fg_bg() -> SystemTheme | None:
    colorfgbg = os.environ.get("COLORFGBG")
    if not colorfgbg:
        return None
    parts = colorfgbg.split(";")
    if not parts:
        return None
    bg = parts[-1]
    if not bg:
        return None
    try:
        bg_num = int(bg)
    except ValueError:
        return None
    if bg_num < 0 or bg_num > 15:
        return None
    return "dark" if bg_num <= 6 or bg_num == 8 else "light"
