"""Task management module. Port of: src/task/"""

import uuid

from hare.task.types import Task, TaskStatus, TaskResult
from hare.task.manager import TaskManager


def generate_task_id() -> str:
    """Generate a unique task ID."""
    return str(uuid.uuid4())
