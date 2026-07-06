"""Port of: src/commands/tasks/ — List and manage background tasks."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "tasks"
DESCRIPTION = "List and manage background tasks"
ALIASES: list[str] = []


async def call(args: str, messages: list[dict[str, Any]], **ctx: Any) -> dict[str, Any]:
    """Show task queue status."""
    task_manager = ctx.get("task_manager")
    tasks: list[dict[str, Any]] = []

    if task_manager:
        try:
            if callable(getattr(task_manager, "list_tasks", None)):
                tasks = task_manager.list_tasks()
            elif isinstance(task_manager, list):
                tasks = task_manager
        except Exception:
            pass

    if not tasks:
        return {"type": "tasks", "display_text": "No running tasks."}

    lines = ["# Active Tasks\n"]
    # Extract IDs from messages
    tracked_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, dict) and msg.get("type") == "task_status":
            tid = msg.get("task_id", "")
            if tid:
                tracked_ids.add(tid)

    for i, t in enumerate(tasks):
        if isinstance(t, dict):
            tid = t.get("id", f"task-{i}")
            name = t.get("name", t.get("subject", f"Task {i+1}"))
            status = t.get("status", "running")
            status_icon = {"running": "⏳", "completed": "✓", "failed": "✗", "pending": "○"}.get(status, "?")
            lines.append(f"  {status_icon} **{name}** — `{status}`")
        else:
            lines.append(f"  - {t}")

    lines.append("")
    lines.append(f"Total: {len(tasks)} task(s)")

    return {"type": "tasks", "display_text": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argument_hint": "",
    }
