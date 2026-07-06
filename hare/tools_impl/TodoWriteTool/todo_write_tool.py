"""
TodoWriteTool – manage a structured task list.

Port of: src/tools/TodoWriteTool/TodoWriteTool.ts
"""

from __future__ import annotations

from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext

TODO_WRITE_TOOL_NAME = "TodoWrite"


# Global todo state
_todos: list[dict[str, Any]] = []


class _TodoWriteTool(ToolBase):
    name = TODO_WRITE_TOOL_NAME
    aliases = ["todo", "todos"]
    search_hint = "manage a structured task list"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {
                                "type": "string",
                                "description": "The task description (imperative form: 'Fix bug')",
                            },
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "cancelled",
                                ],
                            },
                            "activeForm": {
                                "type": "string",
                                "description": "Present continuous form shown during execution (e.g. 'Fixing bug')",
                            },
                        },
                        "required": ["id", "content", "status", "activeForm"],
                    },
                    "description": "Array of TODO items to update or create.",
                },
                "merge": {
                    "type": "boolean",
                    "description": (
                        "Whether to merge with existing todos. "
                        "If false, replaces all existing todos."
                    ),
                },
            },
            "required": ["todos"],
        }

    def output_schema(self) -> dict[str, Any] | None:
        return {
            "type": "object",
            "properties": {
                "oldTodos": {
                    "type": "array",
                    "description": "The todos before the update",
                },
                "newTodos": {
                    "type": "array",
                    "description": "The todos after the update",
                },
                "verificationNudgeNeeded": {
                    "type": "boolean",
                    "description": "Whether the user should verify all tasks are complete",
                },
            },
        }

    def validate_input(self, input: dict[str, Any]) -> Any:
        todos = input.get("todos", [])
        if not isinstance(todos, list):
            from hare.tool import ValidationResultError

            return ValidationResultError(
                result=False,
                message="todos must be an array",
                error_code=1,
            )
        for item in todos:
            if not isinstance(item, dict):
                from hare.tool import ValidationResultError

                return ValidationResultError(
                    result=False,
                    message="Each todo item must be an object",
                    error_code=2,
                )
            if "content" not in item and "id" in item:
                # Allow status-only updates for existing items
                pass
            elif "content" not in item:
                from hare.tool import ValidationResultError

                return ValidationResultError(
                    result=False,
                    message="Each todo item must have a 'content' field",
                    error_code=3,
                )
        from hare.tool import ValidationResultOK

        return ValidationResultOK()

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def prompt(self, options: dict[str, Any]) -> str:
        return (
            "Create and manage a structured task list for your current coding session."
        )

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return "Update TODO list"

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return TODO_WRITE_TOOL_NAME

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        """Update the TODO list."""
        global _todos

        # Capture old state for output_schema
        old_todos = list(_todos)

        new_todos = args.get("todos", [])
        merge = args.get("merge", False)

        if merge:
            existing_by_id = {t["id"]: t for t in _todos}
            for item in new_todos:
                todo_id = item.get("id", "")
                if todo_id in existing_by_id:
                    existing = existing_by_id[todo_id]
                    if "content" in item:
                        existing["content"] = item["content"]
                    if "status" in item:
                        existing["status"] = item["status"]
                    if "activeForm" in item:
                        existing["activeForm"] = item["activeForm"]
                else:
                    existing_by_id[todo_id] = item
            _todos = list(existing_by_id.values())
        else:
            _todos = list(new_todos)

        # Check if all tasks are completed — add verification nudge
        all_completed = (
            all(t.get("status") == "completed" for t in _todos) and len(_todos) > 0
        )

        # Build summary
        lines = ["Successfully updated TODOs.\n"]
        for t in _todos:
            status = t.get("status", "pending").upper()
            content = t.get("content", "")
            active_form = t.get("activeForm", "")
            todo_id = t.get("id", "")
            active_str = f" ({active_form})" if active_form else ""
            lines.append(f"- {status}:{active_str} {content} (id: {todo_id})")

        if all_completed:
            lines.append(
                "\nAll tasks are marked as completed. "
                "Please verify the results before reporting the task as done."
            )

        # Also update app_state if available
        if context and context.set_app_state:
            try:
                agent_id = context.agent_id or "default"
                key = f"todos:{agent_id}"

                def _update(state: Any) -> Any:
                    state = dict(state) if isinstance(state, dict) else {}
                    state[key] = list(_todos)
                    return state

                context.set_app_state(_update)
            except Exception:
                pass

        return ToolResult(
            data="\n".join(lines),
            context_modifier=None,
        )


def get_todos(key: str = "") -> list[dict[str, Any]]:
    """Get the current TODO list."""
    return list(_todos)


def set_todos(key: str, todos: list[dict[str, Any]]) -> None:
    """Set the TODO list."""
    global _todos
    _todos = list(todos)


TodoWriteTool = _TodoWriteTool()
