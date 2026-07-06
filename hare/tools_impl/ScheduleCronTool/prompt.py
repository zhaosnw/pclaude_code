"""
ScheduleCronTool prompt and constants.

Port of: src/tools/ScheduleCronTool/prompt.ts
"""

DEFAULT_MAX_AGE_DAYS = 30

CRON_CREATE_TOOL_NAME = "CronCreate"
CRON_DELETE_TOOL_NAME = "CronDelete"
CRON_LIST_TOOL_NAME = "CronList"


def is_kairos_cron_enabled() -> bool:
    """Runtime gate for the cron scheduling system."""
    import os

    return os.environ.get("CLAUDE_CODE_DISABLE_CRON", "").lower() not in ("1", "true")


def is_durable_cron_enabled() -> bool:
    return True


def build_cron_create_description(durable_enabled: bool) -> str:
    if durable_enabled:
        return (
            "Schedule a prompt to run at a future time — either recurring on a cron schedule, "
            "or once at a specific time. Pass durable: true to persist; otherwise session-only."
        )
    return (
        "Schedule a prompt to run at a future time within this Hare session — "
        "either recurring on a cron schedule, or once at a specific time."
    )


def build_cron_create_prompt(durable_enabled: bool) -> str:
    return f"""Schedule a prompt to be enqueued at a future time. Use for both recurring schedules and one-shot reminders.

Uses standard 5-field cron in the user's local timezone: minute hour day-of-month month day-of-week.

## One-shot tasks (recurring: false)
For "remind me at X" requests — fire once then auto-delete.

## Recurring jobs (recurring: true, the default)
For "every N minutes" / "every hour" / "weekdays at 9am" requests.

Recurring tasks auto-expire after {DEFAULT_MAX_AGE_DAYS} days.
Returns a job ID you can pass to {CRON_DELETE_TOOL_NAME}."""


CRON_LIST_DESCRIPTION = "List scheduled cron jobs"


def build_cron_list_prompt(durable_enabled: bool) -> str:
    if durable_enabled:
        return f"List all cron jobs scheduled via {CRON_CREATE_TOOL_NAME}, both durable and session-only."
    return f"List all cron jobs scheduled via {CRON_CREATE_TOOL_NAME} in this session."


CRON_DELETE_DESCRIPTION = "Cancel a scheduled cron job by ID"


def build_cron_delete_prompt(durable_enabled: bool) -> str:
    if durable_enabled:
        return f"Cancel a cron job previously scheduled with {CRON_CREATE_TOOL_NAME}."
    return f"Cancel a cron job previously scheduled with {CRON_CREATE_TOOL_NAME}."
