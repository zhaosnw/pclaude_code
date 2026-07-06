"""Port of: src/tools/TaskGetTool/prompt.ts"""

from __future__ import annotations

from typing import Any

DESCRIPTION = "Get a task by ID from the task list"

# ── static prompt (no flags) ──────────────────────────────────────────────────

PROMPT = """Use this tool to retrieve a task by its ID from the task list.

## When to Use This Tool

- When you need the full description and context before starting work on a task
- To understand task dependencies (what it blocks, what blocks it)
- After being assigned a task, to get complete requirements

## Output

Returns full task details:
- **subject**: Task title
- **description**: Detailed requirements and context
- **status**: 'pending', 'in_progress', or 'completed'
- **blocks**: Tasks waiting on this one to complete
- **blockedBy**: Tasks that must complete before this one can start

## Tips

- After fetching a task, verify its blockedBy list is empty before beginning work.
- Use TaskList to see all tasks in summary form.
"""

# ── dynamic prompt (with agent-swarm support) ─────────────────────────────────

TASK_FIELD_DOC = {
    "task_id": "Task identifier (use with TaskUpdate, TaskGet)",
    "subject": "Brief description of the task",
    "description": "Detailed requirements and context",
    "status": "'pending', 'in_progress', 'completed', or 'failed'",
    "owner": "Agent ID if assigned, empty if available",
    "blockedBy": "List of open task IDs that must be resolved first",
    "blocks": "List of task IDs waiting on this one to complete",
    "metadata": "Arbitrary metadata attached to the task",
    "activeForm": "Present continuous form shown in progress spinners",
    "createdAt": "ISO-8601 creation timestamp",
    "updatedAt": "ISO-8601 last-update timestamp",
}


def get_prompt(*, agent_swarms_enabled: bool = False) -> str:
    """Build the full TaskGet prompt, optionally including swarm-aware sections."""
    teammate_use_case = ""
    if agent_swarms_enabled:
        teammate_use_case = (
            "- When the task is owned by another agent, to understand "
            "what they should be working on\n"
        )

    teammate_tips = ""
    if agent_swarms_enabled:
        teammate_tips = (
            "- If the task owner field does not match your agent ID, "
            "do not modify it without team lead approval\n"
            "- Use the owner field to identify which teammate is "
            "responsible for a blocked task\n"
        )

    return (
        "Use this tool to retrieve a task by its ID from the task list.\n"
        "\n"
        "## When to Use This Tool\n"
        "\n"
        "- When you need the full description and context before starting "
        "work on a task\n"
        "- To understand task dependencies (what it blocks, what blocks it)\n"
        "- After being assigned a task, to get complete requirements\n"
        f"{teammate_use_case}"
        "\n"
        "## Output\n"
        "\n"
        "Returns full task details:\n"
        "- **task_id**: Task identifier (use with TaskUpdate)\n"
        "- **subject**: Task title\n"
        "- **description**: Detailed requirements and context\n"
        "- **status**: 'pending', 'in_progress', 'completed', or 'failed'\n"
        "- **owner**: Agent ID if assigned, empty if available\n"
        "- **blockedBy**: Tasks that must complete before this one can start\n"
        "- **blocks**: Tasks waiting on this one to complete\n"
        "- **metadata**: Arbitrary metadata attached to the task\n"
        "\n"
        "## Tips\n"
        "\n"
        "- After fetching a task, verify its blockedBy list is empty before "
        "beginning work.\n"
        "- Use TaskList to see all tasks in summary form.\n"
        f"{teammate_tips}"
    )


# ── input schema ──────────────────────────────────────────────────────────────

def input_schema() -> dict[str, Any]:
    """Return the JSON Schema for the TaskGet tool input."""
    return {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to look up (returned by TaskCreate or TaskList)",
                "minLength": 1,
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }


# ── output helpers ────────────────────────────────────────────────────────────

def format_task_not_found(task_id: str) -> dict[str, Any]:
    """Produce a standardized 'not found' response."""
    return {
        "task_id": task_id,
        "status": "not_found",
        "message": f"No task found with id '{task_id}'. Use TaskList to see available tasks.",
    }


def format_task_response(task_data: dict[str, Any]) -> dict[str, Any]:
    """Format raw task state into the canonical response shape.

    task_data must at minimum contain 'task_id' and 'status'.
    Unknown keys are preserved, known keys are presented in a
    deterministic order for predictable output.
    """
    ordered_keys = [
        "task_id",
        "subject",
        "description",
        "status",
        "owner",
        "blockedBy",
        "blocks",
        "metadata",
        "activeForm",
        "createdAt",
        "updatedAt",
    ]
    result: dict[str, Any] = {}
    seen: set[str] = set()

    # Emit known keys in canonical order (present in input dict).
    for key in ordered_keys:
        if key in task_data:
            result[key] = task_data[key]
            seen.add(key)

    # Emit any extra keys not in the canonical list.
    for key, value in task_data.items():
        if key not in seen:
            result[key] = value
            seen.add(key)

    return result


# ── validation ────────────────────────────────────────────────────────────────

_ID_MIN_LENGTH = 1
_ID_MAX_LENGTH = 64
# hex token: 4 bytes = 8 hex chars (matches generate_task_id)
_ID_VALID_HEX_LENGTH = 8


def is_valid_task_id(task_id: object) -> bool:
    """Return True when ``task_id`` looks like a plausible task identifier."""
    if not isinstance(task_id, str):
        return False
    stripped = task_id.strip()
    if len(stripped) < _ID_MIN_LENGTH or len(stripped) > _ID_MAX_LENGTH:
        return False
    # Best-effort: task IDs generated by the system are 8-char hex tokens,
    # but user-provided IDs (e.g. "1", "2") are also accepted for compat.
    return True


def normalize_task_id(raw: str) -> str:
    """Strip and lowercase a task ID for case-insensitive lookups."""
    return raw.strip().lower()
