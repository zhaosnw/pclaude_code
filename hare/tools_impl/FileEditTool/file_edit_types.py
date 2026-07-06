"""Structured edit operation types. Port of: src/tools/FileEditTool/types.ts"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class FileEditOperation:
    path: str
    old_string: str
    new_string: str
    mode: Literal["replace", "insert"] = "replace"
