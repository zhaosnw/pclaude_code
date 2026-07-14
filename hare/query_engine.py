"""
QueryEngine owns the query lifecycle and session state for a conversation.

Port of: src/QueryEngine.ts

It extracts the core logic from ask() into a standalone class that can be
used by both the headless/SDK path and (in a future phase) the REPL.

One QueryEngine per conversation. Each submit_message() call starts a new
turn within the same conversation. State (messages, file cache, usage, etc.)
persists across turns.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional
from uuid import uuid4

from hare.bootstrap.state import get_session_id, is_session_persistence_disabled
from hare.commands import get_slash_command_tool_skills
from hare.cost_tracker import get_model_usage, get_total_api_duration, get_total_cost
from hare.query import query, QueryParams
from hare.services.api.logging import (
    accumulate_usage,
    empty_usage,
    update_usage,
)
from hare.tool import CanUseToolFn, Tool, ToolUseContext, ToolUseContextOptions
from hare.app_types.permissions import ToolPermissionContext
from hare.app_types.command import Command
from hare.app_types.message import (
    Message,
)
from hare.utils.cwd import get_cwd, set_cwd
from hare.utils.messages import (
    SYNTHETIC_MESSAGES,
    create_user_message,
    extract_text_content,
    get_content_text,
)


# ---------------------------------------------------------------------------
# QueryEngineConfig
# ---------------------------------------------------------------------------


@dataclass
class QueryEngineConfig:
    cwd: str = ""
    tools: list[Tool] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    mcp_clients: list[Any] = field(default_factory=list)
    agents: list[Any] = field(default_factory=list)
    can_use_tool: Optional[CanUseToolFn] = None
    get_app_state: Optional[Any] = None
    set_app_state: Optional[Any] = None
    initial_messages: Optional[list[Message]] = None
    read_file_cache: dict[str, Any] = field(default_factory=dict)
    custom_system_prompt: Optional[str] = None
    append_system_prompt: Optional[str] = None
    user_specified_model: Optional[str] = None
    fallback_model: Optional[str] = None
    thinking_config: Optional[dict[str, Any]] = None
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    task_budget: Optional[dict[str, float]] = None
    json_schema: Optional[dict[str, Any]] = None
    verbose: bool = False
    replay_user_messages: bool = False
    handle_elicitation: Optional[Any] = None
    include_partial_messages: bool = False
    set_sdk_status: Optional[Any] = None
    abort_controller: Optional[asyncio.Event] = None
    snip_replay: Optional[Any] = None
    permission_context: Optional[ToolPermissionContext] = None


# ---------------------------------------------------------------------------
# SDKMessage types emitted by QueryEngine (simplified)
# ---------------------------------------------------------------------------

SDKMessage = dict[str, Any]


# ---------------------------------------------------------------------------
# QueryEngine
# ---------------------------------------------------------------------------


class QueryEngine:
    """
    QueryEngine owns the query lifecycle and session state for a conversation.

    Mirrors the TypeScript QueryEngine class in src/QueryEngine.ts.
    """

    def __init__(self, config: QueryEngineConfig) -> None:
        self._config = config
        self._mutable_messages: list[Message] = list(config.initial_messages or [])
        self._abort_controller = config.abort_controller or asyncio.Event()
        self._permission_denials: list[dict[str, Any]] = []
        self._total_usage = empty_usage()
        self._has_handled_orphaned_permission = False
        self._read_file_state: dict[str, Any] = dict(config.read_file_cache)
        self._discovered_skill_names: set[str] = set()
        self._loaded_nested_memory_paths: set[str] = set()
        # parentUuid cursor for transcript persistence; continues the chain from
        # the last seeded message when resuming.
        self._last_persisted_uuid: Optional[str] = None
        if config.initial_messages:
            last = config.initial_messages[-1]
            self._last_persisted_uuid = (
                last.get("uuid")
                if isinstance(last, dict)
                else getattr(last, "uuid", None)
            )

    def _wrap_can_use_tool_tracking(self, can_use_tool: CanUseToolFn) -> CanUseToolFn:
        """Record every non-allow permission decision on the engine.

        Mirrors the canUseTool wrapper in QueryEngine.ts (``Track denials for
        SDK reporting``): the denial list is emitted verbatim as the
        ``permission_denials`` field of result messages.
        """
        from hare.utils.messages.system_init import sdk_compat_tool_name

        async def tracked(
            tool: Tool,
            input: dict[str, Any],
            tool_use_context: ToolUseContext,
            assistant_message: Any,
            tool_use_id: str,
            force_decision: Optional[str] = None,
        ) -> Any:
            result = await can_use_tool(
                tool,
                input,
                tool_use_context,
                assistant_message,
                tool_use_id,
                force_decision,
            )
            # TS records every non-allow decision, but its canUseTool only ever
            # returns allow/deny/ask — 'passthrough' is internal to
            # tool.checkPermissions there. In hare it is a terminal "no rule
            # matched" value that headless treats as allowed, so it is not a
            # denial.
            if getattr(result, "behavior", None) in ("deny", "ask"):
                self._permission_denials.append(
                    {
                        "tool_name": sdk_compat_tool_name(tool.name),
                        "tool_use_id": tool_use_id,
                        "tool_input": input,
                    }
                )
            return result

        return tracked

    async def submit_message(
        self,
        prompt: str | list[Any],
        *,
        uuid: Optional[str] = None,
        is_meta: bool = False,
        system_prompt_override: Optional[list[str]] = None,
        user_context_override: Optional[dict[str, str]] = None,
        system_context_override: Optional[dict[str, str]] = None,
        query_source_override: Optional[str] = None,
    ) -> AsyncGenerator[SDKMessage, None]:
        """
        Submit a new user message and yield SDK messages as the model responds.

        Each call starts a new turn within the same conversation.
        """
        config = self._config
        cwd = config.cwd
        commands = config.commands
        tools = config.tools
        mcp_clients = config.mcp_clients
        verbose = config.verbose
        max_turns = config.max_turns
        max_budget_usd = config.max_budget_usd
        can_use_tool = config.can_use_tool
        agents = config.agents or []

        self._discovered_skill_names.clear()
        set_cwd(cwd)
        persist_session = not is_session_persistence_disabled()
        start_time = time.time()

        # Determine model — CLI (--model) > ANTHROPIC_MODEL / settings.json ``model``
        from hare.utils.model import get_main_loop_model, parse_user_specified_model

        if config.user_specified_model:
            main_loop_model = parse_user_specified_model(config.user_specified_model)
        else:
            main_loop_model = get_main_loop_model()

        # Thinking config
        thinking_config = config.thinking_config or {"type": "adaptive"}

        # Build system prompt: assemble the full Claude Code default prompt from
        # all sections (identity, tools, git-safety, environment, ...) and split
        # on the cache boundary into separately-cacheable blocks. get_system_prompt
        # folds custom/append in as sections. An explicit override wins.
        if system_prompt_override is not None:
            system_prompt = system_prompt_override
        else:
            from hare.bootstrap.state import get_is_non_interactive_session
            from hare.constants.prompts import (
                SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
                get_system_prompt,
            )

            full_prompt = get_system_prompt(
                tools=tools,
                main_loop_model=main_loop_model,
                mcp_clients=mcp_clients,
                custom_system_prompt=config.custom_system_prompt,
                append_system_prompt=config.append_system_prompt,
                is_non_interactive=get_is_non_interactive_session(),
            )
            system_prompt = [
                part.strip()
                for part in full_prompt.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
                if part.strip()
            ]

        # Build ToolUseContext
        tool_use_context = ToolUseContext(
            options=ToolUseContextOptions(
                commands=commands,
                debug=False,
                tools=tools,
                verbose=verbose,
                main_loop_model=main_loop_model,
                thinking_config=thinking_config,
                mcp_clients=mcp_clients,
                is_non_interactive_session=True,
                agent_definitions={"activeAgents": agents, "allAgents": []},
                permission_context=config.permission_context,
            ),
            read_file_state=self._read_file_state,
            get_app_state=config.get_app_state,
            set_app_state=config.set_app_state,
            messages=list(self._mutable_messages),
            discovered_skill_names=self._discovered_skill_names,
            loaded_nested_memory_paths=self._loaded_nested_memory_paths,
        )

        # Create user message and push
        if isinstance(prompt, str):
            user_msg = create_user_message(content=prompt)
        else:
            user_msg = create_user_message(content=prompt)
        if uuid:
            user_msg.uuid = uuid
        user_msg.is_meta = is_meta

        self._mutable_messages.append(user_msg)
        self._persist_message(user_msg, persist=persist_session, cwd=cwd)
        messages = list(self._mutable_messages)

        # Yield system init message
        yield {
            "type": "system",
            "subtype": "init",
            "session_id": get_session_id(),
            "tools": [t.name for t in tools],
            "model": main_loop_model,
        }

        # Load skills (cache-only in headless/SDK mode)
        skills = await get_slash_command_tool_skills(get_cwd())

        # Run query loop
        current_message_usage = empty_usage()
        turn_count = 1
        last_stop_reason: Optional[str] = None

        # Track denials for SDK reporting (QueryEngine.ts wraps canUseTool the
        # same way: every non-allow decision is recorded on the result object).
        tracked_can_use_tool = (
            self._wrap_can_use_tool_tracking(can_use_tool) if can_use_tool else None
        )

        query_params = QueryParams(
            messages=messages,
            system_prompt=system_prompt,
            user_context=user_context_override or {},
            system_context=system_context_override or {},
            can_use_tool=tracked_can_use_tool,
            tool_use_context=tool_use_context,
            fallback_model=config.fallback_model,
            query_source=query_source_override or "sdk",
            max_turns=max_turns,
            task_budget=config.task_budget,
        )

        async for message in query(query_params):
            msg_type = getattr(message, "type", None)

            if msg_type == "assistant":
                self._mutable_messages.append(message)
                self._persist_message(message, persist=persist_session, cwd=cwd)
                yield self._normalize_message(message)

            elif msg_type == "user":
                self._mutable_messages.append(message)
                self._persist_message(message, persist=persist_session, cwd=cwd)
                turn_count += 1
                yield self._normalize_message(message)

            elif msg_type == "progress":
                self._mutable_messages.append(message)
                yield self._normalize_message(message)

            elif msg_type == "stream_event":
                event = getattr(message, "event", {})
                event_type = event.get("type", "")
                if event_type == "message_start":
                    current_message_usage = empty_usage()
                    current_message_usage = update_usage(
                        current_message_usage, event.get("message", {}).get("usage")
                    )
                elif event_type == "message_delta":
                    current_message_usage = update_usage(
                        current_message_usage, event.get("usage")
                    )
                    delta = event.get("delta", {})
                    if delta.get("stop_reason"):
                        last_stop_reason = delta["stop_reason"]
                elif event_type == "message_stop":
                    self._total_usage = accumulate_usage(
                        self._total_usage, current_message_usage
                    )
                yield {
                    "type": "stream_event",
                    "session_id": get_session_id(),
                    "event": event,
                }

            elif msg_type == "attachment":
                self._mutable_messages.append(message)
                attachment = getattr(message, "attachment", {})
                if attachment.get("type") == "max_turns_reached":
                    yield {
                        "type": "result",
                        "subtype": "error_max_turns",
                        "is_error": True,
                        "duration_ms": (time.time() - start_time) * 1000,
                        "num_turns": attachment.get("turnCount", turn_count),
                        "stop_reason": last_stop_reason,
                        "session_id": get_session_id(),
                        "total_cost_usd": get_total_cost(),
                        "usage": self._total_usage,
                        "permission_denials": self._permission_denials,
                        "uuid": str(uuid4()),
                    }
                    return

            elif msg_type == "system":
                self._mutable_messages.append(message)
                subtype = getattr(message, "subtype", "")
                if subtype == "compact_boundary":
                    yield {
                        "type": "system",
                        "subtype": "compact_boundary",
                        "session_id": get_session_id(),
                        "uuid": getattr(message, "uuid", ""),
                    }

            # Check USD budget
            if max_budget_usd is not None and get_total_cost() >= max_budget_usd:
                yield {
                    "type": "result",
                    "subtype": "error_max_budget_usd",
                    "is_error": True,
                    "duration_ms": (time.time() - start_time) * 1000,
                    "num_turns": turn_count,
                    "stop_reason": last_stop_reason,
                    "session_id": get_session_id(),
                    "total_cost_usd": get_total_cost(),
                    "usage": self._total_usage,
                    "permission_denials": self._permission_denials,
                    "uuid": str(uuid4()),
                }
                return

        # Extract text result from last assistant message
        text_result = ""
        is_api_error = False
        for msg in reversed(self._mutable_messages):
            if msg.type == "assistant":
                content = msg.message.content
                # Preserve surrounding whitespace in the final result text —
                # get_content_text() .strip()s it, but Claude Code keeps it
                # verbatim (e.g. a whitespace-only or trailing-newline result).
                text = extract_text_content(content, "\n")
                if text and text not in SYNTHETIC_MESSAGES:
                    text_result = text
                    is_api_error = bool(msg.is_api_error_message)
                    break
                if not text_result:
                    is_api_error = bool(msg.is_api_error_message)

        yield {
            "type": "result",
            "subtype": "success",
            "is_error": is_api_error,
            "duration_ms": (time.time() - start_time) * 1000,
            "duration_api_ms": get_total_api_duration(),
            "num_turns": turn_count,
            "result": text_result,
            "stop_reason": last_stop_reason,
            "session_id": get_session_id(),
            "total_cost_usd": get_total_cost(),
            "usage": self._total_usage,
            "model_usage": get_model_usage(),
            "permission_denials": self._permission_denials,
            "uuid": str(uuid4()),
        }

    def _persist_message(self, message: Message, *, persist: bool, cwd: str) -> None:
        """Append one conversation message to the session transcript, continuing
        the parentUuid chain. No-op when persistence is disabled. Restricted to
        user/assistant entries — the conversation core needed for --resume to
        carry context; metadata/system/sidechain entries are out of scope here."""
        if not persist:
            return
        mtype = (
            message.get("type")
            if isinstance(message, dict)
            else getattr(message, "type", None)
        )
        # Mirror TS isLoggableMessage: keep user, assistant, and system
        # (compact_boundary) entries; drop progress and most attachments.
        if mtype not in ("user", "assistant", "system"):
            return
        from hare.utils.session_storage import (
            message_to_transcript_entry,
            record_transcript,
        )

        entry = message_to_transcript_entry(
            message,
            parent_uuid=self._last_persisted_uuid,
            session_id=get_session_id(),
            cwd=cwd,
        )
        record_transcript([entry])
        self._last_persisted_uuid = entry.get("uuid")

    def _normalize_message(self, message: Message) -> SDKMessage:
        """Convert internal message to SDK-compatible dict."""
        return {
            "type": message.type,
            "uuid": getattr(message, "uuid", ""),
            "session_id": get_session_id(),
            "message": message,
        }

    def interrupt(self) -> None:
        """Abort the current query."""
        self._abort_controller.set()

    def get_messages(self) -> list[Message]:
        return list(self._mutable_messages)

    def get_read_file_state(self) -> dict[str, Any]:
        return self._read_file_state

    def get_session_id(self) -> str:
        return get_session_id()

    def set_model(self, model: str) -> None:
        self._config.user_specified_model = model

    def to_client_event(self, event: SDKMessage) -> dict[str, Any]:
        """Normalize SDK events to a plain dict for external consumers."""
        return dict(event)


# ---------------------------------------------------------------------------
# ask()  – convenience wrapper around QueryEngine for one-shot usage
# ---------------------------------------------------------------------------


async def ask(
    *,
    commands: list[Command],
    prompt: str | list[Any],
    cwd: str,
    tools: list[Tool],
    mcp_clients: list[Any] | None = None,
    verbose: bool = False,
    can_use_tool: Optional[CanUseToolFn] = None,
    mutable_messages: list[Message] | None = None,
    get_app_state: Optional[Any] = None,
    set_app_state: Optional[Any] = None,
    custom_system_prompt: Optional[str] = None,
    append_system_prompt: Optional[str] = None,
    user_specified_model: Optional[str] = None,
    fallback_model: Optional[str] = None,
    thinking_config: Optional[dict[str, Any]] = None,
    max_turns: Optional[int] = None,
    max_budget_usd: Optional[float] = None,
    agents: list[Any] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[SDKMessage, None]:
    """
    Sends a single prompt to the Hare API and returns the response.
    Assumes non-interactive usage – will not ask for permissions.

    Convenience wrapper around QueryEngine for one-shot usage.
    """
    engine = QueryEngine(
        QueryEngineConfig(
            cwd=cwd,
            tools=tools,
            commands=commands,
            mcp_clients=mcp_clients or [],
            agents=agents or [],
            can_use_tool=can_use_tool,
            get_app_state=get_app_state,
            set_app_state=set_app_state,
            initial_messages=mutable_messages or [],
            read_file_cache={},
            custom_system_prompt=custom_system_prompt,
            append_system_prompt=append_system_prompt,
            user_specified_model=user_specified_model,
            fallback_model=fallback_model,
            thinking_config=thinking_config,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            verbose=verbose,
        )
    )

    async for msg in engine.submit_message(prompt):
        yield msg
