"""Port of: src/utils/hooks/hooksConfigManager.ts"""

from __future__ import annotations
from typing import Any

# In-memory hook registry
_hooks_config: dict[str, list[dict[str, Any]]] = {}

# Event metadata catalog — mirrors TS getHookEventMetadata()
HOOK_EVENT_METADATA: dict[str, dict[str, Any]] = {
    "PreToolUse": {
        "summary": "Before a tool is executed",
        "description": "Fires before any tool is run — can modify inputs or block execution.",
        "matchersRequired": True,
    },
    "PostToolUse": {
        "summary": "After a tool succeeds",
        "description": "Fires after a tool completes successfully.",
        "matchersRequired": True,
    },
    "PostToolUseFailure": {
        "summary": "After a tool fails",
        "description": "Fires after a tool execution fails.",
        "matchersRequired": True,
    },
    "PermissionDenied": {
        "summary": "When permission is denied",
        "description": "Fires when user denies a tool permission request.",
        "matchersRequired": True,
    },
    "Notification": {
        "summary": "System notification event",
        "description": "Fires for system-level notification hooks.",
        "matchersRequired": False,
    },
    "UserPromptSubmit": {
        "summary": "When user submits a prompt",
        "description": "Fires when the user submits a message to the model.",
        "matchersRequired": False,
    },
    "SessionStart": {
        "summary": "Session start",
        "description": "Fires once at the beginning of a new session.",
        "matchersRequired": False,
    },
    "Stop": {
        "summary": "After model response completes",
        "description": "Fires when the model finishes responding — can prevent continuation.",
        "matchersRequired": True,
    },
    "StopFailure": {
        "summary": "After model response fails",
        "description": "Fires when the model response encounters an error.",
        "matchersRequired": True,
    },
    "SubagentStart": {
        "summary": "When a subagent starts",
        "description": "Fires when a subagent (Agent tool) begins execution.",
        "matchersRequired": True,
    },
    "SubagentStop": {
        "summary": "When a subagent finishes",
        "description": "Fires when a subagent completes.",
        "matchersRequired": True,
    },
    "PreCompact": {
        "summary": "Before conversation compaction",
        "description": "Fires before the conversation is compacted.",
        "matchersRequired": False,
    },
    "PostCompact": {
        "summary": "After conversation compaction",
        "description": "Fires after the conversation is compacted.",
        "matchersRequired": False,
    },
    "PermissionRequest": {
        "summary": "When permission is requested",
        "description": "Fires when a permission request is shown to the user.",
        "matchersRequired": True,
    },
    "SessionEnd": {
        "summary": "Session end",
        "description": "Fires when a session ends.",
        "matchersRequired": False,
    },
    "Setup": {
        "summary": "After initial setup",
        "description": "Fires after the initial session setup is complete.",
        "matchersRequired": False,
    },
    "TeammateIdle": {
        "summary": "When a teammate is idle",
        "description": "Fires when a teammate agent has no active tasks.",
        "matchersRequired": True,
    },
    "TaskCreated": {
        "summary": "When a task is created",
        "description": "Fires when a new task is created for an agent.",
        "matchersRequired": True,
    },
    "TaskCompleted": {
        "summary": "When a task completes",
        "description": "Fires when a task is completed by an agent.",
        "matchersRequired": True,
    },
    "Elicitation": {
        "summary": "When elicitation is needed",
        "description": "Fires when the model needs user input during a turn.",
        "matchersRequired": False,
    },
    "ElicitationResult": {
        "summary": "After elicitation result",
        "description": "Fires after user provides elicitation input.",
        "matchersRequired": False,
    },
    "ConfigChange": {
        "summary": "When configuration changes",
        "description": "Fires when settings are modified during a session.",
        "matchersRequired": False,
    },
    "InstructionsLoaded": {
        "summary": "When instructions are loaded",
        "description": "Fires when CLAUDE.md instructions are loaded.",
        "matchersRequired": False,
    },
    "WorktreeCreate": {
        "summary": "When a worktree is created",
        "description": "Fires when a git worktree is created.",
        "matchersRequired": False,
    },
    "WorktreeRemove": {
        "summary": "When a worktree is removed",
        "description": "Fires when a git worktree is removed.",
        "matchersRequired": False,
    },
    "CwdChanged": {
        "summary": "When working directory changes",
        "description": "Fires when the current working directory is changed.",
        "matchersRequired": False,
    },
    "FileChanged": {
        "summary": "When a file changes on disk",
        "description": "Fires when a watched file is modified on disk.",
        "matchersRequired": False,
    },
    "Resume": {
        "summary": "When a session is resumed",
        "description": "Fires when an existing session is resumed.",
        "matchersRequired": False,
    },
}


def register_hooks(event: str, hooks: list[dict[str, Any]]) -> None:
    _hooks_config.setdefault(event, []).extend(hooks)


def get_hooks_for_event(event: str) -> list[dict[str, Any]]:
    return _hooks_config.get(event, [])


def clear_hooks() -> None:
    _hooks_config.clear()


def group_hooks_by_event_and_matcher(
    hooks: list[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Group hooks by event name, then by matcher string (or '' for no matcher)."""
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for hook in hooks:
        event = hook.get("event", "")
        matcher = hook.get("matcher") or hook.get("if") or ""
        result.setdefault(event, {}).setdefault(matcher, []).append(hook)
    return result


def get_sorted_matchers_for_event(
    event: str,
    hooks_by_event_and_matcher: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[str]:
    """Return sorted list of matcher strings for an event."""
    matchers = hooks_by_event_and_matcher.get(event, {})
    return sorted(matchers.keys())


def get_matcher_metadata(event: str, matcher: str) -> dict[str, Any]:
    """Return metadata for a specific event+matcher combination."""
    meta = HOOK_EVENT_METADATA.get(event, {})
    return {
        "event": event,
        "matcher": matcher,
        "summary": meta.get("summary", ""),
        "description": meta.get("description", ""),
    }
