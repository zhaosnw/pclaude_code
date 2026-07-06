"""Prompt submit / queue execution — port of `handlePromptSubmit.ts` (wiring stubs)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from hare.utils.abort_controller import create_abort_controller
from hare.utils.graceful_shutdown import graceful_shutdown_sync
from hare.utils.message_queue_manager import QueuedCommand, enqueue


@dataclass
class PromptInputHelpers:
    set_cursor_offset: Callable[[int], None]
    clear_buffer: Callable[[], None]
    reset_history: Callable[[], None]


@dataclass
class HandlePromptSubmitParams:
    helpers: PromptInputHelpers
    messages: list[Any]
    main_loop_model: str
    ide_selection: Any | None
    query_source: str
    commands: list[Any]
    query_guard: Any
    set_tool_jsx: Callable[[Any], None]
    get_tool_use_context: Callable[..., Any]
    set_user_input_on_processing: Callable[..., None]
    set_abort_controller: Callable[[Any], None]
    on_query: Callable[..., Awaitable[None]]
    set_app_state: Callable[..., None]
    on_input_change: Callable[[str], None]
    set_pasted_contents: Callable[[Any], None]
    input: str | None = None
    mode: str = "prompt"
    pasted_contents: dict[int, Any] = field(default_factory=dict)
    queued_commands: list[QueuedCommand] | None = None
    skip_slash_commands: bool = False
    is_external_loading: bool = False
    on_before_query: Callable[..., Awaitable[bool]] | None = None
    can_use_tool: Callable[..., Any] | None = None
    abort_controller: Any | None = None
    has_interruptible_tool_in_progress: bool = False


def _exit() -> None:
    graceful_shutdown_sync(0)


async def handle_prompt_submit(params: HandlePromptSubmitParams) -> None:
    """Orchestrate paste expansion, immediate commands, queueing, and `executeUserInput`."""
    if params.queued_commands:
        # Delegate to full pipeline when `process_user_input` + profiler wired.
        return

    text = params.input or ""
    if not text.strip():
        return

    trimmed = text.strip()
    if not params.skip_slash_commands and trimmed in (
        "exit",
        "quit",
        ":q",
        ":q!",
        ":wq",
        ":wq!",
    ):
        ex = next(
            (c for c in params.commands if getattr(c, "name", None) == "exit"), None
        )
        if ex:
            await handle_prompt_submit(replace(params, input="/exit"))
        else:
            _exit()
        return

    if params.query_guard.is_active or params.is_external_loading:
        if params.mode not in ("prompt", "bash"):
            return
        if params.has_interruptible_tool_in_progress and params.abort_controller:
            params.abort_controller.abort("interrupt")
        enqueue(
            QueuedCommand(
                value=text,
                mode=params.mode,
                skip_slash_commands=params.skip_slash_commands,
            )
        )
        params.on_input_change("")
        params.helpers.set_cursor_offset(0)
        params.set_pasted_contents({})
        params.helpers.clear_buffer()
        params.helpers.reset_history()
        return

    ac = create_abort_controller()
    params.set_abort_controller(ac)
    try:
        from hare.utils.process_user_input.process_user_input import process_user_input  # type: ignore[import-not-found]
    except ImportError:

        async def process_user_input(*_a: Any, **_k: Any) -> Any:
            return type("R", (), {"messages": [], "shouldQuery": False})()

    ctx = params.get_tool_use_context(params.messages, [], ac, params.main_loop_model)
    await process_user_input(
        input=text,
        context=ctx,
        messages=params.messages,
        set_tool_jsx=params.set_tool_jsx,
        mode=params.mode,
    )
