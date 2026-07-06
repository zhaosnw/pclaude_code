"""
Hook types — events, definitions, callbacks, matchers, and results.

Port of: src/types/hooks.ts (291 lines)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

# ---------------------------------------------------------------------------
# Hook events
# ---------------------------------------------------------------------------

HookEvent = Literal[
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


# ---------------------------------------------------------------------------
# Hook definition
# ---------------------------------------------------------------------------


@dataclass
class HookDefinition:
    event: HookEvent
    command: str = ""
    script: str = ""
    timeout: float = 30.0
    blocking: bool = True
    matcher: Optional[str] = None  # tool name or '*' for all


# ---------------------------------------------------------------------------
# Hook callback / matcher
# ---------------------------------------------------------------------------


@dataclass
class HookCallbackMatcher:
    """Callback-based hook matcher — for SDK-registered hooks."""

    event: HookEvent
    callback: Any = None  # Callable[[HookInput], HookJSONOutput | None]
    matcher: Optional[str] = None
    priority: int = 0


@dataclass
class PluginHookMatcher:
    """Plugin-based hook matcher — loaded from plugin manifests."""

    event: HookEvent
    command: str = ""
    plugin_root: str = ""
    timeout: float = 30.0
    blocking: bool = True
    matcher: Optional[str] = None


RegisteredHookMatcher = Union[HookCallbackMatcher, PluginHookMatcher]


# ---------------------------------------------------------------------------
# Hook input
# ---------------------------------------------------------------------------


@dataclass
class HookInput:
    event: HookEvent
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: Any = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    agent_id: str = ""
    cwd: str = ""
    permission_mode: str = ""
    trigger: str = ""  # 'manual' | 'auto' | 'clear'
    custom_instructions: Optional[str] = None


# ---------------------------------------------------------------------------
# Hook JSON output (what hooks return on stdout)
# ---------------------------------------------------------------------------


@dataclass
class HookJSONOutput:
    """Standard hook output format (JSON on stdout)."""

    continue_: bool = True  # False = block the operation
    stop_reason: Optional[str] = None
    decision: Optional[Literal["allow", "deny", "ask"]] = None
    reason: Optional[str] = None
    message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Hook results
# ---------------------------------------------------------------------------


@dataclass
class HookResult:
    success: bool = True
    output: str = ""
    error: str = ""
    should_block: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedHookResult:
    """Result from executing all hooks for an event."""

    blocked: bool = False
    messages: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hook context
# ---------------------------------------------------------------------------


@dataclass
class HookContext:
    event: HookEvent
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: Any = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    agent_id: str = ""


# ---------------------------------------------------------------------------
# Hook progress
# ---------------------------------------------------------------------------


@dataclass
class HookProgress:
    """Payload attached to ProgressMessage emitted by hook executors."""

    command: Optional[str] = None
    prompt_text: Optional[str] = None
    duration_ms: Optional[int] = None
    hook_event: Optional[HookEvent] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt hooks (user_prompt_submit)
# ---------------------------------------------------------------------------


@dataclass
class PromptRequest:
    """The prompt being submitted (for user_prompt_submit hooks)."""

    text: str = ""
    mode: str = "prompt"


@dataclass
class PromptResponse:
    """Modified prompt returned by a hook."""

    text: str = ""
    additional_messages: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hook blocking error
# ---------------------------------------------------------------------------


class HookBlockingError(Exception):
    """Raised when a hook blocks the operation."""

    def __init__(
        self, message: str, hook_event: str = "", stop_reason: Optional[str] = None
    ) -> None:
        super().__init__(message)
        self.hook_event = hook_event
        self.stop_reason = stop_reason


# ---------------------------------------------------------------------------
# Per-event hook output schemas
# ---------------------------------------------------------------------------


def hook_json_output_schema() -> dict[str, Any]:
    """JSON Schema for hook output validation."""
    return {
        "type": "object",
        "properties": {
            "continue": {"type": "boolean"},
            "stop_reason": {"type": "string"},
            "decision": {"enum": ["allow", "deny", "ask"]},
            "reason": {"type": "string"},
            "message": {"type": "string"},
        },
    }
