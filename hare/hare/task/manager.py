"""
Task manager – creates, tracks, and executes tasks.

Port of: src/task/taskManager.ts
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from hare.task.types import Task, TaskResult, TaskStatus


@dataclass
class TaskManager:
    tasks: dict[str, Task] = field(default_factory=dict)

    def create_task(
        self,
        description: str,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        task_id = str(uuid.uuid4())[:8]
        now = time.time()
        task = Task(
            id=task_id,
            description=description,
            parent_id=parent_id,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self.tasks[task_id] = task
        if parent_id and parent_id in self.tasks:
            self.tasks[parent_id].subtask_ids.append(task_id)
        return task

    def update_status(self, task_id: str, status: TaskStatus) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].status = status
            self.tasks[task_id].updated_at = time.time()

    def complete_task(self, task_id: str, result: TaskResult) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].status = "completed"
            self.tasks[task_id].result = result
            self.tasks[task_id].updated_at = time.time()

    def fail_task(self, task_id: str, error: str) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].status = "failed"
            self.tasks[task_id].result = TaskResult(success=False, error=error)
            self.tasks[task_id].updated_at = time.time()

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> list[Task]:
        return list(self.tasks.values())

    def get_pending_tasks(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status == "pending"]
