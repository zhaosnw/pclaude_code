"""Task Create Tool — create background tasks with proper lifecycle.

Port of: src/tools/TaskCreateTool/TaskCreateTool.ts
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional

TOOL_NAME = "TaskCreate"
DESCRIPTION = "Create a new background task for long-running work"

# Shared in-memory task registry
_tasks: dict[str, "TaskState"] = {}


@dataclass
class TaskState:
    task_id: str
    description: str
    prompt: str
    model: str = ""
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[str] = None
    parent_agent_id: Optional[str] = None
    _process: Optional[asyncio.Task[Any]] = field(default=None, repr=False)


def generate_task_id() -> str:
    return secrets.token_hex(4)


def get_task(task_id: str) -> Optional[TaskState]:
    return _tasks.get(task_id)


def get_all_tasks() -> list[TaskState]:
    return list(_tasks.values())


def register_task(state: TaskState) -> None:
    _tasks[state.task_id] = state


def deregister_task(task_id: str) -> None:
    _tasks.pop(task_id, None)


async def _execute_task(state: TaskState) -> None:
    """Execute a task asynchronously. Override with actual agent execution."""
    try:
        state.status = "running"
        state.started_at = time.time()
        # In a full implementation, this would spawn a sub-agent query
        await asyncio.sleep(0.1)  # placeholder
        state.status = "completed"
        state.result = f"Task '{state.description}' completed."
    except asyncio.CancelledError:
        state.status = "cancelled"
    except Exception as e:
        state.status = "failed"
        state.error = str(e)
    finally:
        state.completed_at = time.time()


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short (3-5 word) description of the task",
            },
            "prompt": {
                "type": "string",
                "description": "The task prompt to execute in the background",
            },
            "model": {
                "type": "string",
                "description": "Model to use for the task (defaults to session model)",
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent type to use for the task",
            },
        },
        "required": ["description", "prompt"],
    }


async def call(
    description: str,
    prompt: str,
    model: str = "",
    subagent_type: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Create and start a background task."""
    task_id = generate_task_id()
    parent_agent_id = kwargs.get("_agent_id", kwargs.get("agent_id", ""))

    state = TaskState(
        task_id=task_id,
        description=description,
        prompt=prompt,
        model=model or kwargs.get("current_model", ""),
        parent_agent_id=parent_agent_id if parent_agent_id else None,
    )
    register_task(state)

    # Start execution
    loop = asyncio.get_event_loop()
    state._process = loop.create_task(_execute_task(state))

    return {
        "task_id": task_id,
        "status": state.status,
        "description": description,
        "message": f"Task '{description}' created and started (id={task_id}).",
    }
