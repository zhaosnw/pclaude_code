"""Render options for Ink / terminal UI. Port of: renderOptions.ts"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RenderOptions:
    stdout_columns: int = 80
    stdout_rows: int = 24
    patch_stdout: bool = True
