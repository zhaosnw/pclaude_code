"""
Test harness for ``hare.query.core.query`` (Python port of ``query.ts``).

Inject ``QueryDeps`` with fake ``call_model`` / compaction, matching the TS
``QueryParams.deps`` pattern from ``src/query/deps.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Callable, Optional
from unittest.mock import AsyncMock

from hare.query.deps import QueryDeps
from hare.tool import (
    ToolBase,
    ToolResult,
    ToolUseContext,
    ToolUseContextOptions,
    get_empty_tool_permission_context,
)
from hare.app_types.message import APIMessage, AssistantMessage, Message
from hare.app_types.permissions import PermissionAllowDecision


class _AbortSignal:
    def __init__(self, *, aborted: bool = False, reason: Any = None) -> None:
        self.aborted = aborted
        self.reason = reason


class _AbortController:
    def __init__(self, *, aborted: bool = False, reason: Any = None) -> None:
        self.signal = _AbortSignal(aborted=aborted, reason=reason)


class MutableAbortController:
    """Abort signal the tool layer can flip mid-turn (mirrors user Ctrl+C mid-tool)."""

    def __init__(self) -> None:
        self.signal = _AbortSignal(aborted=False, reason=None)


class NoopReadTool(ToolBase):
    """Minimal concurrency-safe read tool for exercising the tool → recurse path."""

    name: str = "noop_read"
    aliases: list[str] = []
    search_hint: str = "noop"

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def call(
        self,
        args: dict[str, Any],
        context: Any,
        can_use_tool: Any,
        parent_message: AssistantMessage,
        on_progress: Any = None,
    ) -> ToolResult:
        return ToolResult(data={"ok": True, "echo": args})


def make_app_state(
    *,
    permission_mode: str = "default",
) -> Any:
    base = get_empty_tool_permission_context()
    try:
        perm_ctx = replace(base, mode=permission_mode)  # type: ignore[arg-type]
    except TypeError:
        perm_ctx = base
    return SimpleNamespace(
        tool_permission_context=perm_ctx,
        mcp={"tools": [], "clients": []},
        fast_mode=False,
        effort_value=None,
        advisor_model=None,
    )


def make_tool_use_context(
    *,
    tools: Optional[list[Any]] = None,
    main_loop_model: str = "claude-sonnet-4-20250514",
    aborted: bool = False,
    abort_reason: Any = None,
    abort_controller: Any = None,
) -> ToolUseContext:
    opts = ToolUseContextOptions(
        main_loop_model=main_loop_model,
        tools=tools or [NoopReadTool()],
        thinking_config={"type": "disabled"},
        mcp_clients=[],
        is_non_interactive_session=True,
        agent_definitions={"activeAgents": [], "allowedAgentTypes": []},
    )
    controller = abort_controller or _AbortController(
        aborted=aborted, reason=abort_reason
    )
    return ToolUseContext(
        options=opts,
        abort_controller=controller,
        get_app_state=lambda: make_app_state(),
        set_app_state=lambda _f: None,
        set_in_progress_tool_use_ids=lambda _f: None,
        set_response_length=lambda _f: None,
        update_file_history_state=lambda _f: None,
        update_attribution_state=lambda _f: None,
        messages=[],
    )


async def allow_all_can_use_tool(
    *_args: Any, **_kwargs: Any
) -> PermissionAllowDecision:
    return PermissionAllowDecision(behavior="allow")


def assistant_text_only(text: str) -> AssistantMessage:
    return AssistantMessage(
        message=APIMessage(
            role="assistant",
            content=[{"type": "text", "text": text}],
            stop_reason="end_turn",
        ),
    )


def assistant_with_tool_use(
    *, tool_id: str, tool_name: str = "noop_read"
) -> AssistantMessage:
    return AssistantMessage(
        message=APIMessage(
            role="assistant",
            content=[
                {"type": "text", "text": "use tool"},
                {"type": "tool_use", "id": tool_id, "name": tool_name, "input": {}},
            ],
            stop_reason="tool_use",
        ),
    )


def assistant_withheld_max_output_tokens() -> AssistantMessage:
    """Assistant error withheld during stream (``api_error == max_output_tokens``)."""
    return AssistantMessage(
        message=APIMessage(
            role="assistant",
            content=[{"type": "text", "text": "(truncated)"}],
            stop_reason="max_tokens",
        ),
        is_api_error_message=True,
        api_error="max_output_tokens",
    )


def assistant_withheld_prompt_too_long() -> AssistantMessage:
    return AssistantMessage(
        message=APIMessage(
            role="assistant",
            content=[{"type": "text", "text": "prompt too long for this model"}],
            stop_reason="error",
        ),
        is_api_error_message=True,
        api_error="invalid_request",
    )


def assistant_withheld_media_too_large() -> AssistantMessage:
    return AssistantMessage(
        message=APIMessage(
            role="assistant",
            content=[{"type": "text", "text": "image too large for upload"}],
            stop_reason="error",
        ),
        is_api_error_message=True,
        api_error="image_error",
    )


def assistant_api_error_completed(
    *, api_error: str = "rate_limit_error"
) -> AssistantMessage:
    """Non-withheld API error → ``finish('completed')`` after stop-hook skip (TS L1258-1265)."""
    return AssistantMessage(
        message=APIMessage(
            role="assistant",
            content=[{"type": "text", "text": "Service temporarily unavailable"}],
            stop_reason="error",
        ),
        is_api_error_message=True,
        api_error=api_error,
    )


class AbortDuringToolCall(ToolBase):
    """Sets abort on the context mid-tool so the post-tools path hits ``aborted_tools``."""

    name: str = "abort_mid_tool"
    aliases: list[str] = []
    search_hint: str = "abort"

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any,
        parent_message: AssistantMessage,
        on_progress: Any = None,
    ) -> ToolResult:
        ac = getattr(context, "abort_controller", None)
        sig = getattr(ac, "signal", None) if ac is not None else None
        if sig is not None:
            sig.aborted = True
        return ToolResult(data={"aborted": True})


class FallbackTriggeredError(Exception):
    """Shape-checked by ``_is_fallback_triggered_error`` (name match)."""

    def __init__(self) -> None:
        super().__init__("Model fallback triggered: model-a -> model-b")


async def noop_microcompact(
    messages: list[Message], *_args: Any, **_kwargs: Any
) -> dict[str, Any]:
    return {"messages": list(messages)}


async def noop_autocompact(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {}


def fixed_uuid_factory(value: str) -> Callable[[], str]:
    return lambda: value


def make_deps(
    *,
    call_model: Any,
    uuid_value: str = "00000000-0000-4000-8000-000000000001",
) -> QueryDeps:
    return QueryDeps(
        call_model=call_model,
        microcompact=AsyncMock(side_effect=noop_microcompact),
        autocompact=AsyncMock(side_effect=noop_autocompact),
        uuid=fixed_uuid_factory(uuid_value),
    )


async def drain_query(gen: AsyncGenerator[Any, None]) -> list[Any]:
    out: list[Any] = []
    async for x in gen:
        out.append(x)
    return out


@dataclass
class TerminalCapture:
    terminal: Any = None

    def on_terminal(self, t: Any) -> None:
        self.terminal = t
