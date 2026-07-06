"""
SDK control protocol schemas — control_request, control_response, stdin/stdout.

Port of: src/entrypoints/sdk/controlSchemas.ts + controlTypes.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Control request (SDK host -> CLI)
# ---------------------------------------------------------------------------


@dataclass
class SDKControlRequest:
    type: Literal["control_request"] = "control_request"
    request_id: str = ""
    request: dict[str, Any] = field(default_factory=dict)  # subtype + data


# ---------------------------------------------------------------------------
# Control response (CLI -> SDK host)
# ---------------------------------------------------------------------------


@dataclass
class SDKControlResponse:
    type: Literal["control_response"] = "control_response"
    response: dict[str, Any] = field(
        default_factory=dict
    )  # subtype, request_id, response/error
    session_id: str = ""


# ---------------------------------------------------------------------------
# Control cancel request (CLI -> SDK host)
# ---------------------------------------------------------------------------


@dataclass
class SDKControlCancelRequest:
    type: Literal["control_cancel_request"] = "control_cancel_request"
    request_id: str = ""


# ---------------------------------------------------------------------------
# Elicitation
# ---------------------------------------------------------------------------


@dataclass
class ElicitationRequest:
    type: Literal["elicitation"] = "elicitation"
    request_id: str = ""
    schema_: dict[str, Any] | None = None
    message: str = ""


@dataclass
class ElicitationResponse:
    type: Literal["elicitation_response"] = "elicitation_response"
    request_id: str = ""
    response: Any = None


# ---------------------------------------------------------------------------
# Can use tool (permission check)
# ---------------------------------------------------------------------------


@dataclass
class CanUseToolRequest:
    subtype: Literal["can_use_tool"] = "can_use_tool"
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = ""


@dataclass
class CanUseToolResponse:
    behavior: Literal["allow", "deny"] = "allow"
    updated_input: dict[str, Any] | None = None
    updated_permissions: list[dict[str, Any]] | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------


@dataclass
class InitializeRequest:
    subtype: Literal["initialize"] = "initialize"


@dataclass
class InitializeResponse:
    commands: list[dict[str, Any]] = field(default_factory=list)
    output_style: str = "normal"
    available_output_styles: list[str] = field(default_factory=lambda: ["normal"])
    models: list[dict[str, Any]] = field(default_factory=list)
    account: dict[str, Any] = field(default_factory=dict)
    pid: int = 0


# ---------------------------------------------------------------------------
# Set model
# ---------------------------------------------------------------------------


@dataclass
class SetModelRequest:
    subtype: Literal["set_model"] = "set_model"
    model: str = ""


# ---------------------------------------------------------------------------
# Set permission mode
# ---------------------------------------------------------------------------


@dataclass
class SetPermissionModeRequest:
    subtype: Literal["set_permission_mode"] = "set_permission_mode"
    mode: str = ""


# ---------------------------------------------------------------------------
# Interrupt
# ---------------------------------------------------------------------------


@dataclass
class InterruptRequest:
    subtype: Literal["interrupt"] = "interrupt"


# ---------------------------------------------------------------------------
# Set max thinking tokens
# ---------------------------------------------------------------------------


@dataclass
class SetMaxThinkingTokensRequest:
    subtype: Literal["set_max_thinking_tokens"] = "set_max_thinking_tokens"
    max_thinking_tokens: int | None = None


# ---------------------------------------------------------------------------
# Stdin message (CLI -> SDK host via stdout)
# ---------------------------------------------------------------------------

StdoutMessageType = Literal[
    "stream_event",
    "assistant",
    "user",
    "result",
    "system",
    "control_request",
    "control_response",
    "control_cancel_request",
    "error",
    "progress",
]


@dataclass
class StdoutMessage:
    type: StdoutMessageType = "stream_event"
    data: Any = None


# ---------------------------------------------------------------------------
# Stdin message (SDK host -> CLI via stdin)
# ---------------------------------------------------------------------------

StdinMessageType = Literal[
    "user",
    "control_response",
    "control_cancel_request",
    "update_environment_variables",
    "keep_alive",
]


@dataclass
class StdinMessage:
    type: StdinMessageType = "user"
    message: dict[str, Any] | None = None
    session_id: str = ""


# ---------------------------------------------------------------------------
# Stream event
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    type: Literal["stream_event"] = "stream_event"
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    uuid: str = ""


# ---------------------------------------------------------------------------
# Control message (for backward compat)
# ---------------------------------------------------------------------------


@dataclass
class ControlMessage:
    type: str = ""
    action: str = ""
    payload: dict[str, Any] | None = None
