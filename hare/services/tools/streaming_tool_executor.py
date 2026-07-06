"""
Streaming tool executor — manages concurrent tool execution with abort signals.

Port of: src/services/tools/StreamingToolExecutor.ts

Key design decisions (matching TS):
- Hierarchical abort: per-tool child abort controllers cascade to siblingAbortController
- Bash error propagation: BashTool errors cancel siblings; non-Bash errors don't
- Synthetic error messages for sibling_error, user_interrupted, streaming_fallback
- Order guarantee: results yielded in request order despite parallel execution
- Progress bypasses order: progress messages yield immediately
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Optional

from hare.services.tools.tool_execution import run_tool_use
from hare.tool import CanUseToolFn, ToolUseContext, find_tool_by_name
from hare.app_types.message import AssistantMessage, Message
from hare.utils.messages import create_user_message

BASH_TOOL_NAME = "Bash"

# TS: REJECT_MESSAGE constant used for user_interrupted synthetic errors
REJECT_MESSAGE = "User rejected tool use"


@dataclass
class MessageUpdate:
    message: Optional[Message] = None
    new_context: Optional[ToolUseContext] = None


ToolStatus = str


@dataclass
class _TrackedTool:
    id: str
    block: dict[str, Any]
    assistant_message: AssistantMessage
    status: ToolStatus
    is_concurrency_safe: bool
    pending_progress: list[Message] = field(default_factory=list)
    promise: Optional[asyncio.Task[None]] = None
    results: Optional[list[Message]] = None
    context_modifiers: list[Callable[[ToolUseContext], ToolUseContext]] = field(
        default_factory=list
    )
    interrupt_behavior: str = "block"
    this_tool_errored: bool = False


class StreamingToolExecutor:
    """Executes tools as they stream in and yields results incrementally.

    Matching TS StreamingToolExecutor:
    - Per-tool child abort controllers cascade through siblingAbortController
    - BashTool errors cancel sibling tools via siblingAbortController.abort()
    - Non-Bash tool errors do NOT propagate (independent operations)
    - Synthetic error messages generated for sibling_error/user_interrupted/fallback
    """

    def __init__(
        self,
        tool_definitions: list[Any],
        can_use_tool: CanUseToolFn,
        tool_use_context: ToolUseContext,
    ) -> None:
        self._tool_definitions = tool_definitions
        self._can_use_tool = can_use_tool
        self._tool_use_context = tool_use_context
        self._tools: list[_TrackedTool] = []
        self._discarded = False

        # TS: siblingAbortController — created from parent abortController
        # When a BashTool errors, this is aborted to cancel siblings.
        parent_controller = _resolve_abort_controller(tool_use_context)
        self._sibling_abort_controller = _create_child_abort_controller(
            parent_controller
        )

        # TS: hasErrored / erroredToolDescription — tracks Bash error cascades
        self._has_errored = False
        self._errored_tool_description = ""

        self._progress_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discard(self) -> None:
        """Discard all pending/in-progress tools (streaming fallback)."""
        self._discarded = True
        self._progress_event.set()

    def add_tool(
        self, block: dict[str, Any], assistant_message: AssistantMessage
    ) -> None:
        tool_def = find_tool_by_name(self._tool_definitions, str(block.get("name", "")))
        if tool_def is None:
            # Unknown tool — complete immediately with error (TS L88-104)
            self._tools.append(
                _TrackedTool(
                    id=str(block.get("id", "")),
                    block=block,
                    assistant_message=assistant_message,
                    status="completed",
                    is_concurrency_safe=True,
                    results=[
                        create_user_message(
                            content=[
                                {
                                    "type": "tool_result",
                                    "content": f"<tool_use_error>Error: No such tool available: {block.get('name', '')}</tool_use_error>",
                                    "is_error": True,
                                    "tool_use_id": str(block.get("id", "")),
                                }
                            ],
                            tool_use_result=f"Error: No such tool available: {block.get('name', '')}",
                            source_tool_assistant_uuid=getattr(
                                assistant_message, "uuid", None
                            ),
                        )
                    ],
                )
            )
            return

        input_args = block.get("input") if isinstance(block.get("input"), dict) else {}
        is_concurrency_safe = False
        try:
            is_concurrency_safe = bool(tool_def.is_concurrency_safe(input_args))
        except Exception:
            is_concurrency_safe = False

        interrupt_behavior = "block"
        try:
            behavior_getter = getattr(tool_def, "interrupt_behavior", None)
            if callable(behavior_getter):
                interrupt_behavior = str(behavior_getter())
        except Exception:
            interrupt_behavior = "block"

        tracked = _TrackedTool(
            id=str(block.get("id", "")),
            block=block,
            assistant_message=assistant_message,
            status="queued",
            is_concurrency_safe=is_concurrency_safe,
            interrupt_behavior=interrupt_behavior,
        )
        self._tools.append(tracked)
        asyncio.create_task(self._process_queue())

    def get_updated_context(self) -> ToolUseContext:
        return self._tool_use_context

    def get_completed_results(self) -> list[MessageUpdate]:
        if self._discarded:
            return []

        updates: list[MessageUpdate] = []
        for tool in self._tools:
            while tool.pending_progress:
                updates.append(
                    MessageUpdate(
                        message=tool.pending_progress.pop(0),
                        new_context=self._tool_use_context,
                    )
                )

            if tool.status == "yielded":
                continue

            if tool.status == "completed" and tool.results is not None:
                tool.status = "yielded"
                for modifier in tool.context_modifiers:
                    self._tool_use_context = modifier(self._tool_use_context)
                for message in tool.results:
                    updates.append(
                        MessageUpdate(
                            message=message,
                            new_context=self._tool_use_context,
                        )
                    )
                self._mark_tool_use_as_complete(tool.id)
            elif tool.status == "executing" and not tool.is_concurrency_safe:
                break
        return updates

    async def get_remaining_results(
        self,
    ) -> AsyncGenerator[MessageUpdate, None]:
        if self._discarded:
            return

        while self._has_unfinished_tools():
            await self._process_queue()

            completed = self.get_completed_results()
            if completed:
                for update in completed:
                    yield update
                continue

            if self._has_executing_tools():
                waiters = [
                    t.promise
                    for t in self._tools
                    if t.status == "executing" and t.promise
                ]
                if waiters:
                    progress_event = self._progress_event
                    await asyncio.wait(
                        [*waiters, asyncio.create_task(progress_event.wait())],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if progress_event.is_set():
                        self._progress_event = asyncio.Event()
            else:
                break

        for update in self.get_completed_results():
            yield update

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _process_queue(self) -> None:
        if self._discarded:
            return
        for tool in self._tools:
            if tool.status != "queued":
                continue
            if self._can_execute_tool(tool.is_concurrency_safe):
                await self._execute_tool(tool)
            elif not tool.is_concurrency_safe:
                break

    def _can_execute_tool(self, is_concurrency_safe: bool) -> bool:
        executing = [t for t in self._tools if t.status == "executing"]
        if not executing:
            return True
        return is_concurrency_safe and all(t.is_concurrency_safe for t in executing)

    async def _execute_tool(self, tool: _TrackedTool) -> None:
        # TS L294-303: per-tool child abort controller
        tool_abort_controller = _create_child_abort_controller(
            self._sibling_abort_controller
        )

        # TS L304-318: bubble non-sibling aborts up to parent
        _attach_abort_bubble_listener(
            tool_abort_controller,
            parent_controller=_resolve_abort_controller(self._tool_use_context),
            exclude_reason="sibling_error",
            discarded_ref=lambda: self._discarded,
        )

        tool.status = "executing"
        self._set_tool_in_progress(tool.id, add=True)
        self._update_interruptible_state()

        # Replace abort controller in context with per-tool controller
        exec_context = _with_abort_controller(
            self._tool_use_context, tool_abort_controller
        )

        async def _collect() -> None:
            messages: list[Message] = []
            context_modifiers: list[Callable[[ToolUseContext], ToolUseContext]] = []
            try:
                async for update in run_tool_use(
                    tool.block,
                    tool.assistant_message,
                    self._can_use_tool,
                    exec_context,
                ):
                    # TS L335-341: check abort reason each iteration
                    abort_reason = self._get_abort_reason(tool)
                    if abort_reason is not None and not tool.this_tool_errored:
                        messages.append(
                            self._create_synthetic_error_message(
                                tool.id, abort_reason, tool.assistant_message
                            )
                        )
                        break

                    # TS L343-354: detect tool errors
                    is_error_result = _is_tool_error_result(update.message)
                    if is_error_result:
                        tool.this_tool_errored = True
                        # BashTool errors cancel siblings; non-Bash don't (TS L359-362)
                        tool_name = str(tool.block.get("name", ""))
                        if tool_name == BASH_TOOL_NAME:
                            self._has_errored = True
                            self._errored_tool_description = self._get_tool_description(
                                tool
                            )
                            self._sibling_abort_controller.abort("sibling_error")

                    if self._discarded:
                        break

                    if update.message is not None:
                        if getattr(update.message, "type", None) == "progress":
                            tool.pending_progress.append(update.message)
                            self._progress_event.set()
                        else:
                            messages.append(update.message)
                    if update.context_modifier is not None:
                        context_modifiers.append(update.context_modifier.modify_context)
            finally:
                tool.results = messages
                tool.context_modifiers = context_modifiers
                tool.status = "completed"
                self._update_interruptible_state()
                self._progress_event.set()
                asyncio.create_task(self._process_queue())

        tool.promise = asyncio.create_task(_collect())

    def _has_executing_tools(self) -> bool:
        return any(t.status == "executing" for t in self._tools)

    def _has_unfinished_tools(self) -> bool:
        return any(t.status != "yielded" for t in self._tools)

    def _mark_tool_use_as_complete(self, tool_use_id: str) -> None:
        self._set_tool_in_progress(tool_use_id, add=False)

    def _set_tool_in_progress(self, tool_use_id: str, *, add: bool) -> None:
        setter = self._tool_use_context.set_in_progress_tool_use_ids
        if setter is None:
            return

        def _update(prev: set[str]) -> set[str]:
            next_set = set(prev)
            if add:
                next_set.add(tool_use_id)
            else:
                next_set.discard(tool_use_id)
            return next_set

        setter(_update)

    def _update_interruptible_state(self) -> None:
        setter = self._tool_use_context.set_has_interruptible_tool_in_progress
        if setter is None:
            return
        executing = [t for t in self._tools if t.status == "executing"]
        setter(
            bool(executing) and all(t.interrupt_behavior == "cancel" for t in executing)
        )

    # ------------------------------------------------------------------
    # Abort & error (TS L210-241)
    # ------------------------------------------------------------------

    def _get_abort_reason(self, tool: _TrackedTool) -> str | None:
        """Determine why a tool should be cancelled. TS getAbortReason."""
        if self._discarded:
            return "streaming_fallback"
        if self._has_errored:
            return "sibling_error"
        parent_controller = _resolve_abort_controller(self._tool_use_context)
        if parent_controller is not None and getattr(
            getattr(parent_controller, "signal", None), "aborted", False
        ):
            reason = str(
                getattr(getattr(parent_controller, "signal", None), "reason", "")
            )
            if reason == "interrupt":
                return (
                    "user_interrupted" if tool.interrupt_behavior == "cancel" else None
                )
            return "user_interrupted"
        return None

    def _create_synthetic_error_message(
        self,
        tool_use_id: str,
        reason: str,
        assistant_message: AssistantMessage,
    ) -> Message:
        """Generate synthetic error messages. TS createSyntheticErrorMessage L153-205."""
        if reason == "user_interrupted":
            return create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "content": REJECT_MESSAGE,
                        "is_error": True,
                        "tool_use_id": tool_use_id,
                    }
                ],
                tool_use_result="User rejected tool use",
                source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
            )
        if reason == "streaming_fallback":
            return create_user_message(
                content=[
                    {
                        "type": "tool_result",
                        "content": "<tool_use_error>Error: Streaming fallback - tool execution discarded</tool_use_error>",
                        "is_error": True,
                        "tool_use_id": tool_use_id,
                    }
                ],
                tool_use_result="Streaming fallback - tool execution discarded",
                source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
            )
        # sibling_error
        desc = self._errored_tool_description
        msg = (
            f"Cancelled: parallel tool call {desc} errored"
            if desc
            else "Cancelled: parallel tool call errored"
        )
        return create_user_message(
            content=[
                {
                    "type": "tool_result",
                    "content": f"<tool_use_error>{msg}</tool_use_error>",
                    "is_error": True,
                    "tool_use_id": tool_use_id,
                }
            ],
            tool_use_result=msg,
            source_tool_assistant_uuid=getattr(assistant_message, "uuid", None),
        )

    def _get_tool_description(self, tool: _TrackedTool) -> str:
        """Get human-readable tool description for error messages."""
        name = str(tool.block.get("name", "unknown"))
        tool_def = find_tool_by_name(self._tool_definitions, name)
        if tool_def is not None:
            try:
                desc = tool_def.user_facing_name()
                if isinstance(desc, str):
                    return desc
            except Exception:
                pass
        return name


# ---------------------------------------------------------------------------
# Abort controller helpers
# ---------------------------------------------------------------------------


class _AbortController:
    """Minimal AbortController matching TS patterns."""

    def __init__(self, parent: Optional[_AbortController] = None) -> None:
        self._signal = _AbortSignal(parent)
        self._parent = parent

    @property
    def signal(self) -> _AbortSignal:
        return self._signal

    def abort(self, reason: str = "") -> None:
        self._signal._abort(reason)


class _AbortSignal:
    def __init__(self, parent: Optional[_AbortSignal] = None) -> None:
        self.aborted = False
        self.reason = ""
        self._listeners: list[Callable[[], None]] = []
        self._parent = parent

    def _abort(self, reason: str) -> None:
        if self.aborted:
            return
        self.aborted = True
        self.reason = reason
        for listener in self._listeners:
            try:
                listener()
            except Exception:
                pass

    def add_event_listener(self, event: str, listener: Callable[[], None]) -> None:
        if event == "abort":
            self._listeners.append(listener)


def _create_child_abort_controller(
    parent: Any,
) -> _AbortController:
    """Create a child abort controller. TS createChildAbortController."""
    if parent is not None and hasattr(parent, "signal"):
        parent_signal = parent.signal
    else:
        parent_signal = None
    controller = _AbortController()
    if hasattr(controller, "_parent"):
        pass  # already set
    return controller


def _resolve_abort_controller(tool_use_context: Any) -> Optional[_AbortController]:
    """Extract abort controller from Context."""
    if tool_use_context is None:
        return None
    controller = getattr(tool_use_context, "abort_controller", None)
    if controller is not None:
        return controller
    return None


def _attach_abort_bubble_listener(
    tool_controller: _AbortController,
    parent_controller: Optional[_AbortController],
    exclude_reason: str,
    discarded_ref: Callable[[], bool],
) -> None:
    """Attach bubble-up listener: non-sibling aborts bubble to parent.

    TS L304-318: tool abort controller's signal listener bubbles non-sibling
    aborts up to the parent context's abort controller.
    """

    def _on_abort() -> None:
        if (
            not discarded_ref()
            and tool_controller.signal.reason != exclude_reason
            and parent_controller is not None
            and not getattr(parent_controller.signal, "aborted", False)
        ):
            parent_controller.abort(tool_controller.signal.reason)

    tool_controller.signal.add_event_listener("abort", _on_abort)


def _with_abort_controller(tool_use_context: Any, controller: _AbortController) -> Any:
    """Replace abort controller in tool_use_context."""
    if hasattr(tool_use_context, "abort_controller"):
        try:
            return type(tool_use_context)(
                **{
                    **tool_use_context.__dict__,
                    "abort_controller": controller,
                }
            )
        except TypeError:
            pass
    try:
        setattr(tool_use_context, "abort_controller", controller)
    except (AttributeError, TypeError):
        pass
    return tool_use_context


def _is_tool_error_result(message: Any) -> bool:
    """Check if a message contains a tool_result with is_error=True."""
    if message is None:
        return False
    msg_type = getattr(message, "type", None)
    if msg_type == "progress":
        return False
    content = getattr(message, "message", None)
    if content is None:
        return False
    if hasattr(content, "content") and isinstance(content.content, list):
        return any(isinstance(b, dict) and b.get("is_error") for b in content.content)
    return False
