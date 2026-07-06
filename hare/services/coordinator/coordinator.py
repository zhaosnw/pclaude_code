"""
Coordinator mode — multi-agent orchestration with worker agents.

Port of: src/services/coordinator/coordinator.ts

Manages a coordinator + workers pattern where the coordinator agent
delegates tasks to worker agents and synthesizes their results.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional


@dataclass
class WorkerTask:
    """A task dispatched to a worker agent."""
    task_id: str
    description: str
    prompt: str
    model: str = ""
    status: str = "pending"
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CoordinatorConfig:
    """Configuration for coordinator mode."""
    max_workers: int = 4
    default_model: str = ""
    auto_delegate: bool = True
    worker_timeout: float = 300.0  # 5 minutes


@dataclass
class Coordinator:
    """Orchestrates multi-agent conversations with worker delegation."""

    context: Any = None
    config: CoordinatorConfig = field(default_factory=CoordinatorConfig)
    _running: bool = False
    _workers: dict[str, WorkerTask] = field(default_factory=dict)
    _conversation: list[dict[str, Any]] = field(default_factory=list)
    _scratchpad: str = ""

    async def start(self) -> None:
        """Start coordinator mode."""
        self._running = True

    async def stop(self) -> None:
        """Stop coordinator mode and cancel pending workers."""
        self._running = False
        for task in self._workers.values():
            if task.status in ("pending", "running"):
                task.status = "cancelled"
                task.error = "Coordinator stopped"

    async def delegate_task(self, description: str, prompt: str, model: str = "") -> str:
        """Delegate a task to a worker agent. Returns the task_id."""
        import secrets
        task_id = secrets.token_hex(4)
        task = WorkerTask(
            task_id=task_id, description=description, prompt=prompt,
            model=model or self.config.default_model,
        )
        self._workers[task_id] = task

        if len(self._workers) > self.config.max_workers:
            return task_id  # Queued, not started yet

        asyncio.ensure_future(self._run_worker(task))
        return task_id

    async def _run_worker(self, task: WorkerTask) -> None:
        """Execute a worker task and store its result."""
        task.status = "running"
        try:
            # In full implementation, spawns a sub-agent query
            await asyncio.sleep(0.1)  # placeholder for actual agent execution
            task.status = "completed"
            task.result = f"Worker completed: {task.description}"
        except asyncio.CancelledError:
            task.status = "cancelled"
        except Exception as e:
            task.status = "failed"
            task.error = str(e)

    async def process_message(self, message: str) -> AsyncIterator[dict[str, Any]]:
        """Process a user message and yield response events."""
        self._conversation.append({"role": "user", "content": message})

        if self.config.auto_delegate and self._should_delegate(message):
            task_id = await self.delegate_task(
                description=f"Process: {message[:100]}",
                prompt=message,
            )
            yield {"type": "worker_delegated", "task_id": task_id, "content": "Task delegated to worker agent."}

        yield {"type": "text", "content": ""}

    def _should_delegate(self, message: str) -> bool:
        """Determine if a message should be delegated to a worker."""
        delegatable_keywords = [
            "research", "analyze", "audit", "review", "search",
            "find all", "comprehensive", "thorough", "explore",
        ]
        return any(kw in message.lower() for kw in delegatable_keywords)

    def get_worker_status(self, task_id: str) -> Optional[dict[str, Any]]:
        """Get the status of a worker task."""
        task = self._workers.get(task_id)
        if task is None:
            return None
        return {
            "task_id": task.task_id, "description": task.description,
            "status": task.status, "result": task.result, "error": task.error,
        }

    def get_all_workers(self) -> list[dict[str, Any]]:
        """Get status for all workers."""
        return [self.get_worker_status(tid) for tid in self._workers]

    def get_worker_count(self) -> dict[str, int]:
        """Get worker counts by status."""
        counts: dict[str, int] = {}
        for task in self._workers.values():
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts

    def set_scratchpad(self, content: str) -> None:
        """Set the coordinator's scratchpad for cross-worker context."""
        self._scratchpad = content

    def get_scratchpad(self) -> str:
        """Get the coordinator's scratchpad content."""
        return self._scratchpad

    def get_conversation_history(self) -> list[dict[str, Any]]:
        """Get the coordinator's conversation history."""
        return list(self._conversation)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    @property
    def active_workers(self) -> int:
        return sum(1 for t in self._workers.values() if t.status == "running")


_instance: Coordinator | None = None


def get_coordinator() -> Coordinator:
    global _instance
    if _instance is None:
        _instance = Coordinator()
    return _instance


def reset_coordinator() -> None:
    global _instance
    _instance = None
