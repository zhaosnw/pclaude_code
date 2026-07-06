"""
TodoWriteTool – main implementation.

Port of: src/tools/TodoWriteTool/TodoWriteTool.ts
"""

from __future__ import annotations
from typing import Any
from hare.tools_impl.TodoWriteTool.todo_write_tool import get_todos, set_todos

TOOL_NAME = "TodoWrite"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {"type": "string"},
                    },
                    "required": ["id", "content", "status"],
                },
            },
        },
        "required": ["todos"],
    }


async def call(
    todos: list[dict[str, Any]], session_id: str = "", **kwargs: Any
) -> dict[str, Any]:
    key = session_id or "default"
    current = get_todos(key)
    all_completed = all(t.get("status") == "completed" for t in todos)
    if all_completed:
        set_todos(key, [])
        return {"data": "All todos completed, list cleared."}
    set_todos(key, todos)
    in_progress = [t for t in todos if t.get("status") == "in_progress"]
    pending = [t for t in todos if t.get("status") == "pending"]
    completed = [t for t in todos if t.get("status") == "completed"]
    return {
        "data": f"Updated: {len(in_progress)} in progress, {len(pending)} pending, {len(completed)} completed",
    }
