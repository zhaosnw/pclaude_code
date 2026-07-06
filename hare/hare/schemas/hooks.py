"""
Hook Zod-equivalent schemas for hook configuration validation.

Port of: src/schemas/hooks.ts (222 lines)

Extracted from settings/types.ts to break circular imports between
settings/types and plugins/schemas. Both files import from here.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Hook event types (from agentSdkTypes)
# ---------------------------------------------------------------------------

HOOK_EVENTS = [
    "pre_tool_use",
    "post_tool_use",
    "pre_compact",
    "post_compact",
    "user_prompt_submit",
    "session_start",
    "subagent_start",
    "stop",
    "notification",
    "pre_message",
    "post_message",
]

HookEvent = str  # one of HOOK_EVENTS

SHELL_TYPES = ("bash", "powershell")


# ---------------------------------------------------------------------------
# Hook command schemas — equivalent to Zod schemas in TS
# ---------------------------------------------------------------------------


def _if_condition_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "optional": True,
        "description": (
            "Permission rule syntax to filter when this hook runs "
            '(e.g., "Bash(git *)"). Only runs if the tool call matches '
            "the pattern. Avoids spawning hooks for non-matching commands."
        ),
    }


def _bash_command_hook_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"const": "command", "description": "Shell command hook type"},
            "command": {"type": "string", "description": "Shell command to execute"},
            "if": _if_condition_schema(),
            "shell": {
                "type": "string",
                "enum": list(SHELL_TYPES),
                "optional": True,
                "description": "Shell interpreter. 'bash' uses your $SHELL (bash/zsh/sh); 'powershell' uses pwsh. Defaults to bash.",
            },
            "timeout": {
                "type": "number",
                "optional": True,
                "positive": True,
                "description": "Timeout in seconds for this specific command",
            },
            "statusMessage": {
                "type": "string",
                "optional": True,
                "description": "Custom status message to display in spinner while hook runs",
            },
            "once": {
                "type": "boolean",
                "optional": True,
                "description": "If true, hook runs once and is removed after execution",
            },
            "async": {
                "type": "boolean",
                "optional": True,
                "description": "If true, hook runs in background without blocking",
            },
            "asyncRewake": {
                "type": "boolean",
                "optional": True,
                "description": "If true, hook runs in background and wakes the model on exit code 2. Implies async.",
            },
        },
        "required": ["type", "command"],
    }


def _prompt_hook_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"const": "prompt", "description": "LLM prompt hook type"},
            "prompt": {
                "type": "string",
                "description": "Prompt to evaluate with LLM. Use $ARGUMENTS placeholder for hook input JSON.",
            },
            "if": _if_condition_schema(),
            "timeout": {
                "type": "number",
                "optional": True,
                "positive": True,
                "description": "Timeout in seconds for this specific prompt evaluation",
            },
            "model": {
                "type": "string",
                "optional": True,
                "description": 'Model to use for this prompt hook (e.g., "claude-sonnet-4-6"). If not specified, uses the default small fast model.',
            },
            "statusMessage": {
                "type": "string",
                "optional": True,
                "description": "Custom status message to display in spinner while hook runs",
            },
            "once": {
                "type": "boolean",
                "optional": True,
                "description": "If true, hook runs once and is removed after execution",
            },
        },
        "required": ["type", "prompt"],
    }


def _http_hook_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"const": "http", "description": "HTTP hook type"},
            "url": {
                "type": "string",
                "format": "uri",
                "description": "URL to POST the hook input JSON to",
            },
            "if": _if_condition_schema(),
            "timeout": {
                "type": "number",
                "optional": True,
                "positive": True,
                "description": "Timeout in seconds for this specific request",
            },
            "headers": {
                "type": "object",
                "optional": True,
                "description": "Additional headers to include. Values may reference env vars via $VAR_NAME.",
            },
            "allowedEnvVars": {
                "type": "array",
                "items": {"type": "string"},
                "optional": True,
                "description": "Explicit list of env var names that may be interpolated in header values.",
            },
            "statusMessage": {
                "type": "string",
                "optional": True,
                "description": "Custom status message to display in spinner while hook runs",
            },
            "once": {
                "type": "boolean",
                "optional": True,
                "description": "If true, hook runs once and is removed after execution",
            },
        },
        "required": ["type", "url"],
    }


def _agent_hook_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"const": "agent", "description": "Agentic verifier hook type"},
            "prompt": {
                "type": "string",
                "description": "Prompt describing what to verify. Use $ARGUMENTS placeholder for hook input JSON.",
            },
            "if": _if_condition_schema(),
            "timeout": {
                "type": "number",
                "optional": True,
                "positive": True,
                "description": "Timeout in seconds for agent execution (default 60)",
            },
            "model": {
                "type": "string",
                "optional": True,
                "description": 'Model to use for this agent hook (e.g., "claude-sonnet-4-6"). If not specified, uses Haiku.',
            },
            "statusMessage": {
                "type": "string",
                "optional": True,
                "description": "Custom status message to display in spinner while hook runs",
            },
            "once": {
                "type": "boolean",
                "optional": True,
                "description": "If true, hook runs once and is removed after execution",
            },
        },
        "required": ["type", "prompt"],
    }


# ---------------------------------------------------------------------------
# Discriminated union: HookCommand = BashCommandHook | PromptHook | HttpHook | AgentHook
# ---------------------------------------------------------------------------

HOOK_COMMAND_SCHEMA = {
    "oneOf": [
        _bash_command_hook_schema(),
        _prompt_hook_schema(),
        _http_hook_schema(),
        _agent_hook_schema(),
    ],
    "discriminator": {"propertyName": "type"},
}


def validate_hook_command(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a hook command against the schema. Returns validated data or raises ValueError."""
    hook_type = data.get("type")
    if hook_type == "command":
        _validate_required(data, ["type", "command"])
    elif hook_type == "prompt":
        _validate_required(data, ["type", "prompt"])
    elif hook_type == "http":
        _validate_required(data, ["type", "url"])
    elif hook_type == "agent":
        _validate_required(data, ["type", "prompt"])
    else:
        raise ValueError(
            f"Invalid hook type: {hook_type}. Must be one of: command, prompt, http, agent"
        )
    return data


# ---------------------------------------------------------------------------
# Hook matcher schema
# ---------------------------------------------------------------------------

HOOK_MATCHER_SCHEMA = {
    "type": "object",
    "properties": {
        "matcher": {
            "type": "string",
            "optional": True,
            "description": "String pattern to match (e.g. tool names like 'Write')",
        },
        "hooks": {
            "type": "array",
            "items": HOOK_COMMAND_SCHEMA,
            "description": "List of hooks to execute when the matcher matches",
        },
    },
    "required": ["hooks"],
}


# ---------------------------------------------------------------------------
# Hooks configuration schema: Partial<Record<HookEvent, HookMatcher[]>>
# ---------------------------------------------------------------------------

HOOKS_SCHEMA = {
    "type": "object",
    "properties": {
        event: {"type": "array", "items": HOOK_MATCHER_SCHEMA} for event in HOOK_EVENTS
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Type definitions (inferred from schemas)
# ---------------------------------------------------------------------------

HookCommand = dict[str, Any]  # BashCommandHook | PromptHook | AgentHook | HttpHook
HookMatcher = dict[str, Any]  # { matcher?: string, hooks: HookCommand[] }
HooksSettings = dict[
    str, list[dict[str, Any]]
]  # Partial<Record<HookEvent, HookMatcher[]>>


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_required(data: dict[str, Any], required: list[str]) -> None:
    for key in required:
        if key not in data:
            raise ValueError(f"Missing required field: {key}")


def validate_hooks_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a hooks settings configuration."""
    if not isinstance(data, dict):
        raise ValueError("Hooks settings must be a dict")
    for event, matchers in data.items():
        if event not in HOOK_EVENTS:
            raise ValueError(f"Unknown hook event: {event}")
        if not isinstance(matchers, list):
            raise ValueError(f"Hooks for {event} must be a list")
        for matcher in matchers:
            if not isinstance(matcher, dict):
                raise ValueError(f"Matcher for {event} must be a dict")
            for hook in matcher.get("hooks", []):
                validate_hook_command(hook)
    return data
