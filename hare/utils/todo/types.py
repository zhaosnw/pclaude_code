"""
TODO item types.

Port of: src/utils/todo/types.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TodoStatus = Literal["pending", "in_progress", "completed"]


@dataclass
class TodoItem:
    content: str
    status: TodoStatus = "pending"
    active_form: str = ""
