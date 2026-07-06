"""
Task types.

Port of: src/task/types.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


@dataclass
class TaskResult:
    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    id: str
    description: str
    status: TaskStatus = "pending"
    result: TaskResult | None = None
    parent_id: str | None = None
    subtask_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
