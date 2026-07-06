"""
Forked / subagent query loop orchestration (types and stubs).

Port of: src/utils/forkedAgent.ts — wire `query`, analytics, and ToolUseContext at integration.

This module ensures forked agents:
1. Share identical cache-critical params with the parent to guarantee prompt cache hits
2. Track full usage metrics across the entire query loop
3. Log metrics via the tengu_fork_agent_query event when complete
4. Isolate mutable state to prevent interference with the main agent loop
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from hare.utils.file_state_cache import FileStateCache, clone_file_state_cache
from hare.utils.messages import (
    create_user_message,
    extract_text_content,
    get_last_assistant_message,
)
from hare.utils.debug import log_for_debugging

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Populated from multiple sources; sentinel means "not set"
# ---------------------------------------------------------------------------
_UNSET: Any = object()

# ---------------------------------------------------------------------------
# Cache-safe params — must match parent for prompt cache sharing
# ---------------------------------------------------------------------------

@dataclass
class CacheSafeParams:
    """Parameters that must be identical between fork and parent API requests
    to share the parent's prompt cache.

    The Anthropic API cache key is composed of:
    system prompt, tools, model, messages (prefix), and thinking config.
    """

    system_prompt: Any
    user_context: dict[str, str]
    system_context: dict[str, str]
    tool_use_context: Any
    fork_context_messages: list[Any]


# Slot written after each main-loop turn so post-turn forks can share the
# main loop's prompt cache without each caller threading params through.
_last_cache_safe: CacheSafeParams | None = None


def save_cache_safe_params(params: CacheSafeParams | None) -> None:
    """Store cache-safe params from the main loop (called after each turn)."""
    global _last_cache_safe
    _last_cache_safe = params


def get_last_cache_safe_params() -> CacheSafeParams | None:
    """Retrieve the most recently stored cache-safe params."""
    return _last_cache_safe


# ---------------------------------------------------------------------------
# Forked agent params / result
# ---------------------------------------------------------------------------

@dataclass
class ForkedAgentParams:
    """Parameters for executing a forked/subagent query loop."""

    prompt_messages: list[Any]
    cache_safe_params: CacheSafeParams
    can_use_tool: Callable[..., Any]
    query_source: str
    fork_label: str
    overrides: Any | None = None
    max_output_tokens: int | None = None
    max_turns: int | None = None
    on_message: Callable[[Any], None] | None = None
    skip_transcript: bool = False
    skip_cache_write: bool = False


@dataclass
class ForkedAgentResult:
    """Result of a forked agent query loop."""

    messages: list[Any]
    total_usage: dict[str, int]


# ---------------------------------------------------------------------------
# Create cache-safe params from context
# ---------------------------------------------------------------------------

def create_cache_safe_params(context: Any) -> CacheSafeParams:
    """Extract CacheSafeParams from a REPLHookContext or dict-like.

    To override specific fields (e.g. toolUseContext with cloned file state),
    spread the result and override individual fields.
    """

    def read(name: str) -> Any:
        if isinstance(context, dict):
            return context.get(name)
        return getattr(context, name, None)

    return CacheSafeParams(
        system_prompt=read("system_prompt") or read("systemPrompt"),
        user_context=read("user_context") or read("userContext") or {},
        system_context=read("system_context") or read("systemContext") or {},
        tool_use_context=read("tool_use_context") or read("toolUseContext"),
        fork_context_messages=read("fork_context_messages")
        or read("forkContextMessages")
        or read("messages")
        or [],
    )


# ---------------------------------------------------------------------------
# Allowed-tools wrapper for get_app_state
# ---------------------------------------------------------------------------

def create_get_app_state_with_allowed_tools(
    base_get_app_state: Callable[[], Any],
    allowed_tools: list[str],
) -> Callable[[], Any]:
    """Wrap get_app_state to inject allowed tools into the permission context.

    When allowed_tools is empty the base function is returned unchanged.
    """

    if not allowed_tools:
        return base_get_app_state

    def wrapped() -> Any:
        app_state = base_get_app_state()
        if app_state is None:
            return app_state

        # Read tool_permission_context (attribute or dict key)
        tpc: Any = None
        if isinstance(app_state, dict):
            tpc = app_state.get("tool_permission_context", {})
        else:
            tpc = getattr(app_state, "tool_permission_context", None)

        if tpc is None:
            return app_state

        # Read existing always_allow_rules
        rules: dict[str, Any]
        if isinstance(tpc, dict):
            rules = dict(tpc.get("always_allow_rules", {}))
        else:
            raw = getattr(tpc, "always_allow_rules", None) or {}
            rules = dict(raw) if isinstance(raw, dict) else {}

        # Extend command rule with allowed tools (dedup preserving order)
        existing_cmds: list[str] = list(rules.get("command", []) or [])
        new_cmds: list[str] = list(dict.fromkeys([*existing_cmds, *allowed_tools]))
        rules["command"] = new_cmds

        if isinstance(app_state, dict):
            new_tpc = dict(tpc) if isinstance(tpc, dict) else dict(vars(tpc))
            new_tpc["always_allow_rules"] = rules
            return {**app_state, "tool_permission_context": new_tpc}

        # For object types, return the original; side-effect via attribute mutation
        # is safer but callers should build a new object if isolation is needed.
        return app_state

    return wrapped


# ---------------------------------------------------------------------------
# Prepare forked command context
# ---------------------------------------------------------------------------

@dataclass
class PreparedForkedContext:
    skill_content: str
    modified_get_app_state: Callable[[], Any]
    base_agent: Any
    prompt_messages: list[Any]


async def prepare_forked_command_context(
    command: Any,
    args: str,
    context: Any,
) -> PreparedForkedContext:
    """Prepare the context for executing a forked command/skill.

    Handles skill prompt resolution, allowed-tool injection, agent selection,
    and initial message construction.
    """

    # Resolve the skill prompt with $ARGUMENTS replaced
    skill_prompt = await command.get_prompt_for_command(args, context)
    parts: list[str] = []
    for block in skill_prompt:
        if getattr(block, "type", None) == "text" or (
            isinstance(block, dict) and block.get("type") == "text"
        ):
            t = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else ""
            )
            parts.append(str(t))
    skill_content = "\n".join(parts)

    # Parse allowed tools and create modified get_app_state
    allowed: list[str] = list(getattr(command, "allowed_tools", None) or [])
    modified = create_get_app_state_with_allowed_tools(context.get_app_state, allowed)

    # Resolve agent type
    agent_type = getattr(command, "agent", None) or "general-purpose"
    agents: list[Any] = (
        getattr(
            getattr(context.options, "agent_definitions", None),
            "active_agents",
            None,
        )
        or []
    )

    # Prefer command-specified agent, fall back to general-purpose, then first available
    base_agent: Any = next(
        (a for a in agents if getattr(a, "agent_type", None) == agent_type),
        None,
    )
    if base_agent is None:
        base_agent = next(
            (a for a in agents if getattr(a, "agent_type", None) == "general-purpose"),
            None,
        )
    if base_agent is None and agents:
        base_agent = agents[0]
    if base_agent is None:
        raise RuntimeError("No agent available for forked execution")

    # Build initial prompt: a single user message containing the skill content
    prompt_messages = [{"type": "user", "message": {"content": skill_content}}]

    return PreparedForkedContext(
        skill_content=skill_content,
        modified_get_app_state=modified,
        base_agent=base_agent,
        prompt_messages=prompt_messages,
    )


# ---------------------------------------------------------------------------
# Result text extraction
# ---------------------------------------------------------------------------

def extract_result_text(
    agent_messages: list[Any],
    default_text: str = "Execution completed",
) -> str:
    """Extract the text content from the last assistant message in a forked
    agent result.

    Falls back to `default_text` when no assistant message is found or
    the message has no text content.
    """

    if not agent_messages:
        return default_text

    last_assistant = get_last_assistant_message(agent_messages)
    if last_assistant is None:
        return default_text

    content = getattr(last_assistant, "message", None)
    if content is None:
        return default_text

    blocks = getattr(content, "content", None)
    if blocks is None:
        return default_text

    text = extract_text_content(blocks, "\n")
    return text.strip() or default_text


# ---------------------------------------------------------------------------
# Subagent context creation
# ---------------------------------------------------------------------------

def _parent_get(parent: Any, name: str, default: Any = _UNSET) -> Any:
    """Read a field from an object or dict parent context."""
    if isinstance(parent, dict):
        if default is _UNSET:
            return parent.get(name)
        return parent.get(name, default)
    if default is _UNSET:
        return getattr(parent, name)
    return getattr(parent, name, default)


def _parent_has(parent: Any, name: str) -> bool:
    """Check whether an object or dict parent context has a field."""
    if isinstance(parent, dict):
        return name in parent
    return hasattr(parent, name)


def create_subagent_context(
    parent_context: Any,
    overrides: dict[str, Any] | None = None,
) -> Any:
    """Create an isolated subagent context cloned from parent.

    By default, ALL mutable state is isolated to prevent interference:
    - readFileState: cloned from parent
    - abortController: new controller linked to parent (parent abort propagates)
    - getAppState: wrapped to set shouldAvoidPermissionPrompts
    - All mutation callbacks (setAppState, etc.): no-op

    Callers can:
    - Override specific fields via the overrides parameter
    - Explicitly opt-in to sharing specific callbacks (shareSetAppState, etc.)

    For fork cache-sharing: clones rendered_system_prompt and
    content_replacement_state so the child's API prefix matches the parent's.
    """

    ov = overrides or {}

    # ---- read_file_state ---------------------------------------------------
    read_state = ov.get("read_file_state", _parent_get(parent_context, "read_file_state", None))
    if isinstance(read_state, FileStateCache):
        rfs = clone_file_state_cache(read_state)
    elif hasattr(read_state, "dump") and hasattr(read_state, "load"):
        # Generic clone for file-state-like objects
        rfs = clone_file_state_cache(read_state) if read_state else read_state
    else:
        # Non-clonable (dict or similar) — shallow copy
        rfs = read_state

    # ---- abort_controller --------------------------------------------------
    # Priority: explicit override > shared parent > new child controller
    abort_ctrl = ov.get("abort_controller")
    if abort_ctrl is None:
        if ov.get("share_abort_controller"):
            abort_ctrl = _parent_get(parent_context, "abort_controller", None)
        else:
            # Create a child abort controller linked to parent
            parent_abort = _parent_get(parent_context, "abort_controller", None)
            abort_ctrl = _create_child_abort_controller(parent_abort)

    # ---- get_app_state -----------------------------------------------------
    share_abort = ov.get("share_abort_controller", False)
    if ov.get("get_app_state") is not None:
        get_app_state_fn = ov["get_app_state"]
    elif share_abort:
        # Interactive agent that shares abort controller — can show UI
        get_app_state_fn = _parent_get(parent_context, "get_app_state", lambda: {})
    else:
        # Isolated agent: wrap to mark shouldAvoidPermissionPrompts
        parent_get_app_state = _parent_get(parent_context, "get_app_state", lambda: {})

        def get_app_state_fn() -> Any:
            state = parent_get_app_state()
            if state is None:
                return state
            tpc = (
                state.get("tool_permission_context")
                if isinstance(state, dict)
                else getattr(state, "tool_permission_context", None)
            )
            if tpc is None:
                return state
            should_avoid = (
                tpc.get("shouldAvoidPermissionPrompts")
                if isinstance(tpc, dict)
                else getattr(tpc, "shouldAvoidPermissionPrompts", False)
            )
            if should_avoid:
                return state
            new_tpc = dict(tpc) if isinstance(tpc, dict) else dict(vars(tpc))
            new_tpc["shouldAvoidPermissionPrompts"] = True
            if isinstance(state, dict):
                return {**state, "tool_permission_context": new_tpc}
            return state

    # ---- set_app_state -----------------------------------------------------
    set_app_state = (
        _parent_get(parent_context, "set_app_state", _noop_set_app_state)
        if ov.get("share_set_app_state")
        else _noop_set_app_state
    )

    # ---- set_app_state_for_tasks -------------------------------------------
    # Must always reach the root store even when setAppState is a no-op,
    # otherwise async agents' background tasks are never registered/killed.
    set_app_state_for_tasks = _parent_get(
        parent_context, "set_app_state_for_tasks", None
    )
    if set_app_state_for_tasks is None:
        set_app_state_for_tasks = _parent_get(
            parent_context, "set_app_state", _noop_set_app_state
        )

    # ---- rendered_system_prompt (clone for cache sharing) ------------------
    rendered_sp = ov.get("rendered_system_prompt")
    if rendered_sp is None:
        rendered_sp = _parent_get(parent_context, "rendered_system_prompt", None)

    # ---- content_replacement_state (clone for cache stability) -------------
    crs = ov.get("content_replacement_state")
    if crs is None:
        crs = _parent_get(parent_context, "content_replacement_state", None)
    if crs is not None and hasattr(crs, "copy"):
        crs = crs.copy()
    elif crs is not None and isinstance(crs, dict):
        crs = dict(crs)

    # ---- set_response_length / push_api_metrics ----------------------------
    share_response = ov.get("share_set_response_length", False)
    set_response_length = (
        _parent_get(parent_context, "set_response_length", _noop)
        if share_response
        else _noop
    )
    push_api_metrics_entry = (
        _parent_get(parent_context, "push_api_metrics_entry", None)
        if share_response
        else None
    )

    # ---- agent_id ----------------------------------------------------------
    agent_id = ov.get("agent_id", "a" + str(uuid.uuid4()).replace("-", "")[:16])

    # ---- query_tracking ----------------------------------------------------
    parent_tracking = _parent_get(parent_context, "query_tracking", None)
    parent_depth = -1
    if parent_tracking is not None:
        if isinstance(parent_tracking, dict):
            parent_depth = parent_tracking.get("depth", -1)
        else:
            parent_depth = getattr(parent_tracking, "depth", -1)
    query_tracking = {
        "chainId": str(uuid.uuid4()),
        "depth": parent_depth + 1,
    }

    # Build the context object
    ctx = SimpleNamespace(
        # Mutable state — cloned for isolation
        read_file_state=rfs,
        nested_memory_attachment_triggers=set(),
        loaded_nested_memory_paths=set(),
        dynamic_skill_dir_triggers=set(),
        discovered_skill_names=set(),
        tool_decisions=ov.get("tool_decisions"),
        content_replacement_state=crs,

        # Abort
        abort_controller=abort_ctrl,

        # App state access
        get_app_state=get_app_state_fn,
        set_app_state=set_app_state,
        set_app_state_for_tasks=set_app_state_for_tasks,

        # Denial tracking
        local_denial_tracking=(
            _parent_get(parent_context, "local_denial_tracking", None)
            if ov.get("share_set_app_state")
            else None
        ),

        # Mutation callbacks — no-op by default
        set_in_progress_tool_use_ids=(
            _parent_get(parent_context, "set_in_progress_tool_use_ids", _noop)
            if ov.get("share_set_app_state")
            else _noop
        ),
        set_response_length=set_response_length,
        push_api_metrics_entry=push_api_metrics_entry,
        update_file_history_state=(
            _parent_get(parent_context, "update_file_history_state", _noop)
            if ov.get("share_set_app_state")
            else _noop
        ),
        update_attribution_state=_parent_get(
            parent_context, "update_attribution_state", _noop
        ),

        # UI callbacks — not available for subagents
        add_notification=None,
        set_tool_jsx=None,
        set_stream_mode=None,
        set_sdk_status=None,
        open_message_selector=None,

        # Fields that can be overridden or copied from parent
        options=ov.get("options", _parent_get(parent_context, "options", None)),
        messages=ov.get("messages", _parent_get(parent_context, "messages", [])),
        agent_id=agent_id,
        agent_type=ov.get("agent_type"),
        query_tracking=query_tracking,
        file_reading_limits=_parent_get(parent_context, "file_reading_limits", {}),
        user_modified=_parent_get(parent_context, "user_modified", None),
        critical_system_reminder_experimental=ov.get(
            "critical_system_reminder_EXPERIMENTAL"
        ),
        require_can_use_tool=ov.get("require_can_use_tool", False),
    )

    if rendered_sp is not None:
        setattr(ctx, "rendered_system_prompt", rendered_sp)  # type: ignore[attr-defined]

    return ctx


def _noop(*args: Any, **kwargs: Any) -> None:
    """No-op callable for stubbed mutation callbacks."""


def _noop_set_app_state(*args: Any, **kwargs: Any) -> None:
    """No-op for isolated set_app_state."""


def _create_child_abort_controller(parent: Any) -> Any:
    """Create an abort controller that propagates from the parent.

    When the parent aborts, the child also aborts.  The child can also be
    aborted independently without affecting the parent.
    """
    try:
        from hare.utils.abort_controller import AbortController
    except ImportError:
        # Fallback: simple dict-based controller
        return _FallbackAbortController(parent)

    child = AbortController()
    if parent is not None:
        # Listen for parent abort and propagate
        parent_signal = getattr(parent, "signal", None)
        if parent_signal is not None and hasattr(
            parent_signal, "add_event_listener"
        ):
            parent_signal.add_event_listener("abort", child.abort)
        elif hasattr(parent, "add_event_listener"):
            parent.add_event_listener("abort", child.abort)

    return child


class _FallbackAbortController:
    """Simple dict-based abort controller for when AbortController is unavailable."""

    def __init__(self, parent: Any = None) -> None:
        self._aborted = False
        self._callbacks: list[Callable[[], None]] = []
        self._parent = parent
        if parent is not None and hasattr(parent, "add_event_listener"):
            try:
                parent.add_event_listener("abort", self.abort)
            except Exception:
                pass

    def abort(self) -> None:
        if self._aborted:
            return
        self._aborted = True
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass

    @property
    def signal(self) -> "_FallbackAbortSignal":
        return _FallbackAbortSignal(self)

    def add_event_listener(self, _name: str, cb: Callable[[], None]) -> None:
        self._callbacks.append(cb)


class _FallbackAbortSignal:
    def __init__(self, controller: _FallbackAbortController) -> None:
        self._controller = controller

    @property
    def aborted(self) -> bool:
        return self._controller._aborted


# ---------------------------------------------------------------------------
# SimpleNamespace
# ---------------------------------------------------------------------------

class SimpleNamespace:
    """Minimal namespace object that behaves like a plain object."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def clear(self) -> None:
        """Release read_file_state memory if present."""
        rfs = getattr(self, "read_file_state", None)
        if rfs is not None and hasattr(rfs, "clear"):
            rfs.clear()

    def __repr__(self) -> str:
        items = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"SimpleNamespace({items})"


# ---------------------------------------------------------------------------
# Forked agent query loop
# ---------------------------------------------------------------------------

async def run_forked_agent(params: ForkedAgentParams) -> ForkedAgentResult:
    """Execute a forked/subagent query loop.

    This function:
    1. Uses identical cache-safe params from parent to enable prompt caching
    2. Accumulates usage across all query iterations
    3. Logs tengu_fork_agent_query with full usage when complete

    Args:
        params: Configuration for the forked agent query loop.

    Returns:
        ForkedAgentResult with all yielded messages and total usage stats.

    Raises:
        RuntimeError: When query module is unavailable (fallback to empty result).
    """

    start_time = time.time()
    output_messages: list[Any] = []
    total_usage: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }

    # Unpack cache-safe params
    system_prompt = params.cache_safe_params.system_prompt
    user_context = params.cache_safe_params.user_context or {}
    system_context = params.cache_safe_params.system_context or {}
    tool_use_context = params.cache_safe_params.tool_use_context
    fork_context_messages = params.cache_safe_params.fork_context_messages or []

    # Create isolated context to prevent mutation of parent state
    isolated_ctx = create_subagent_context(tool_use_context, params.overrides)

    # Prepending fork context messages shares the parent's cache prefix.
    # Do NOT filterIncompleteToolCalls here — it drops the whole assistant
    # on partial tool batches, orphaning paired results (API 400).
    # Dangling tool_uses are repaired downstream by ensureToolResultPairing
    # in the query loop, same as the main thread.
    initial_messages: list[Any] = [*fork_context_messages, *params.prompt_messages]

    # Transcript recording (skip when requested or for ephemeral work)
    agent_id: str | None = None
    if not params.skip_transcript:
        try:
            from hare.utils.uuid import create_agent_id

            agent_id = create_agent_id(params.fork_label)
        except ImportError:
            agent_id = "a" + params.fork_label + "-" + str(uuid.uuid4())[:8]

    # Attempt to record initial messages for sidechain transcript
    if agent_id is not None:
        try:
            await _record_sidechain_transcript(
                initial_messages, agent_id, parent_uuid=None
            )
        except Exception as exc:
            log_for_debugging(
                f"Forked agent [{params.fork_label}] failed to record "
                f"initial transcript: {exc}"
            )

    # Track the last recorded message UUID for parent chain continuity
    last_recorded_uuid: str | None = None
    if initial_messages:
        last_msg = initial_messages[-1]
        last_recorded_uuid = (
            last_msg.get("uuid")
            if isinstance(last_msg, dict)
            else getattr(last_msg, "uuid", None)
        )

    try:
        # ---- Run the query loop --------------------------------------------
        try:
            from hare.query.core import query as run_query

            query_params = _build_query_params(
                messages=initial_messages,
                system_prompt=system_prompt,
                user_context=user_context,
                system_context=system_context,
                can_use_tool=params.can_use_tool,
                tool_use_context=isolated_ctx,
                query_source=params.query_source,
                max_output_tokens=params.max_output_tokens,
                max_turns=params.max_turns,
                skip_cache_write=params.skip_cache_write,
            )

            async for item in run_query(query_params):
                # ---- Extract usage from stream events -----------------------
                if _is_stream_event(item):
                    usage = _extract_stream_usage(item)
                    if usage:
                        total_usage = _accumulate_usage(total_usage, usage)
                    # Don't add stream events to output messages
                    continue

                if _is_stream_request_start(item):
                    # Request-start metadata; skip for output
                    continue

                log_for_debugging(
                    f"Forked agent [{params.fork_label}] received message: "
                    f"type={getattr(item, 'type', item.get('type', 'unknown') if isinstance(item, dict) else 'unknown')}"
                )

                output_messages.append(item)

                # Notify callback (for streaming UI)
                if params.on_message is not None:
                    try:
                        params.on_message(item)
                    except Exception as exc:
                        log_for_debugging(
                            f"Forked agent [{params.fork_label}] "
                            f"on_message callback error: {exc}"
                        )

                # Record transcript for recordable message types
                if agent_id is not None:
                    msg_type = _get_message_type(item)
                    if msg_type in ("assistant", "user", "progress"):
                        try:
                            await _record_sidechain_transcript(
                                [item], agent_id, parent_uuid=last_recorded_uuid
                            )
                        except Exception as exc:
                            log_for_debugging(
                                f"Forked agent [{params.fork_label}] failed to "
                                f"record transcript: {exc}"
                            )
                        if msg_type != "progress":
                            last_recorded_uuid = (
                                item.get("uuid")
                                if isinstance(item, dict)
                                else getattr(item, "uuid", None)
                            )

        except ImportError:
            log_for_debugging(
                f"Forked agent [{params.fork_label}] query module not available; "
                f"returning empty result"
            )
            return ForkedAgentResult(
                messages=[],
                total_usage=total_usage,
            )
        except Exception as exc:
            log_for_debugging(
                f"Forked agent [{params.fork_label}] query error: {exc}"
            )
            # Return what we have so far rather than losing progress
            return ForkedAgentResult(
                messages=output_messages,
                total_usage=total_usage,
            )

    finally:
        # Release cloned file state cache memory
        _safe_cleanup(isolated_ctx)
        # Release the cloned fork context messages
        initial_messages.clear()

    duration_ms = int((time.time() - start_time) * 1000)

    log_for_debugging(
        f"Forked agent [{params.fork_label}] finished: "
        f"{len(output_messages)} messages, "
        f"types=[{', '.join(_get_message_type(m) for m in output_messages)}], "
        f"totalUsage: input={total_usage.get('input_tokens', 0)} "
        f"output={total_usage.get('output_tokens', 0)} "
        f"cacheRead={total_usage.get('cache_read_input_tokens', 0)} "
        f"cacheCreate={total_usage.get('cache_creation_input_tokens', 0)}"
    )

    # Log analytics event
    _log_fork_agent_query_event(
        fork_label=params.fork_label,
        query_source=params.query_source,
        duration_ms=duration_ms,
        message_count=len(output_messages),
        total_usage=total_usage,
        query_tracking=getattr(isolated_ctx, "query_tracking", None),
    )

    return ForkedAgentResult(
        messages=output_messages,
        total_usage=total_usage,
    )


# ---------------------------------------------------------------------------
# Internal helpers for run_forked_agent
# ---------------------------------------------------------------------------

def _build_query_params(
    messages: list[Any],
    system_prompt: Any,
    user_context: dict[str, str],
    system_context: dict[str, str],
    can_use_tool: Callable[..., Any],
    tool_use_context: Any,
    query_source: str,
    max_output_tokens: int | None,
    max_turns: int | None,
    skip_cache_write: bool,
) -> Any:
    """Build a QueryParams object for the forked agent query loop.

    Tries to use hare.query.core.QueryParams if available; falls back to
    a SimpleNamespace when the module is not wired.
    """

    try:
        from hare.query.core import QueryParams

        return QueryParams(
            messages=_normalize_prompt_messages(messages),
            system_prompt=system_prompt,
            user_context=user_context,
            system_context=system_context,
            can_use_tool=can_use_tool,
            tool_use_context=tool_use_context,
            query_source=query_source,
            max_output_tokens_override=max_output_tokens,
            max_turns=max_turns,
            skip_cache_write=skip_cache_write,
        )
    except ImportError:
        return SimpleNamespace(
            messages=messages,
            system_prompt=system_prompt,
            user_context=user_context,
            system_context=system_context,
            can_use_tool=can_use_tool,
            tool_use_context=tool_use_context,
            query_source=query_source,
            max_output_tokens_override=max_output_tokens,
            max_turns=max_turns,
            skip_cache_write=skip_cache_write,
        )


def _normalize_prompt_messages(messages: list[Any]) -> list[Any]:
    """Normalize raw prompt messages into proper Message objects.

    Converts dicts with nested {message: {content: ...}} into proper
    UserMessage objects using create_user_message.
    """

    result: list[Any] = []
    for msg in messages:
        if isinstance(msg, dict) and "type" in msg:
            msg_type = msg.get("type")
            if msg_type == "user":
                inner = msg.get("message", {})
                content = inner.get("content", "")
                result.append(create_user_message(content=str(content)))
            else:
                # Pass through non-user messages as-is
                result.append(msg)
        else:
            result.append(msg)
    return result


def _is_stream_event(item: Any) -> bool:
    """Check if an item is a stream event (not a regular message)."""
    msg_type = _get_message_type(item)
    return msg_type == "stream_event"


def _is_stream_request_start(item: Any) -> bool:
    """Check if an item is a stream_request_start metadata event."""
    msg_type = _get_message_type(item)
    return msg_type == "stream_request_start"


def _get_message_type(item: Any) -> str:
    """Extract the 'type' field from a message, handling both objects and dicts."""
    if isinstance(item, dict):
        return str(item.get("type", ""))
    return str(getattr(item, "type", ""))


def _extract_stream_usage(item: Any) -> dict[str, int] | None:
    """Extract usage from a stream_event containing a message_delta.

    The Anthropic API delivers per-turn usage in stream events with
    event type 'message_delta'.
    """

    event: Any = None

    if isinstance(item, dict):
        event = item.get("event")
    else:
        event = getattr(item, "event", None)

    if event is None:
        return None

    event_type: str = ""
    if isinstance(event, dict):
        event_type = str(event.get("type", ""))
    else:
        event_type = str(getattr(event, "type", ""))

    if event_type != "message_delta":
        return None

    usage: Any = None
    if isinstance(event, dict):
        usage = event.get("usage")
    else:
        usage = getattr(event, "usage", None)

    if usage is None:
        return None

    # Extract standard usage fields
    result: dict[str, int] = {}
    if isinstance(usage, dict):
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            val = usage.get(key)
            if isinstance(val, (int, float)):
                result[key] = int(val)
    else:
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            val = getattr(usage, key, None)
            if isinstance(val, (int, float)):
                result[key] = int(val)

    return result if result else None


def _accumulate_usage(
    current: dict[str, int],
    turn: dict[str, int],
) -> dict[str, int]:
    """Accumulate per-turn usage into the running total."""
    result = dict(current)
    for key, val in turn.items():
        result[key] = result.get(key, 0) + val
    return result


async def _record_sidechain_transcript(
    messages: list[Any],
    agent_id: str,
    parent_uuid: str | None = None,
) -> None:
    """Record messages to the sidechain transcript for the given agent.

    Errors are caught and logged; they never propagate.
    """

    try:
        from hare.utils.session_storage import record_sidechain_transcript as _record_fn

        await _record_fn(messages, agent_id, parent_uuid)
    except ImportError:
        pass
    except Exception as exc:
        log_for_debugging(
            f"Forked agent [{agent_id}] sidechain transcript error: {exc}"
        )


def _safe_cleanup(ctx: Any) -> None:
    """Safely clean up isolated context resources.

    Clears read_file_state and any other cleanable resources.
    """

    if ctx is None:
        return
    try:
        if hasattr(ctx, "clear"):
            ctx.clear()
        elif hasattr(getattr(ctx, "read_file_state", None), "clear"):
            ctx.read_file_state.clear()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Analytics event logging
# ---------------------------------------------------------------------------

def _log_fork_agent_query_event(
    fork_label: str,
    query_source: str,
    duration_ms: int,
    message_count: int,
    total_usage: dict[str, int],
    query_tracking: Any = None,
) -> None:
    """Log the tengu_fork_agent_query analytics event with full usage metrics.

    Computes derived metrics (cache hit rate) and dispatches to the
    analytics system if available.
    """

    # Calculate cache hit rate
    total_input = (
        total_usage.get("input_tokens", 0)
        + total_usage.get("cache_creation_input_tokens", 0)
        + total_usage.get("cache_read_input_tokens", 0)
    )
    cache_hit_rate = (
        total_usage.get("cache_read_input_tokens", 0) / total_input
        if total_input > 0
        else 0.0
    )

    try:
        from hare.services.analytics import log_event

        event_data: dict[str, Any] = {
            "forkLabel": fork_label,
            "querySource": query_source,
            "durationMs": duration_ms,
            "messageCount": message_count,
            "inputTokens": total_usage.get("input_tokens", 0),
            "outputTokens": total_usage.get("output_tokens", 0),
            "cacheReadInputTokens": total_usage.get("cache_read_input_tokens", 0),
            "cacheCreationInputTokens": total_usage.get(
                "cache_creation_input_tokens", 0
            ),
            "cacheHitRate": round(cache_hit_rate, 4),
        }

        # Attach query tracking if available
        if query_tracking is not None:
            if isinstance(query_tracking, dict):
                event_data["queryChainId"] = query_tracking.get("chainId", "")
                event_data["queryDepth"] = query_tracking.get("depth", 0)
            else:
                event_data["queryChainId"] = getattr(
                    query_tracking, "chainId", ""
                ) or getattr(query_tracking, "chain_id", "")
                event_data["queryDepth"] = getattr(
                    query_tracking, "depth", 0
                )

        log_event("tengu_fork_agent_query", event_data)

    except ImportError:
        log_for_debugging(
            f"Forked agent [{fork_label}] analytics module not available; "
            f"skipping event logging"
        )
    except Exception as exc:
        log_for_debugging(
            f"Forked agent [{fork_label}] failed to log analytics event: {exc}"
        )


# ---------------------------------------------------------------------------
# Convenience: run a forked agent from saved cache-safe params
# ---------------------------------------------------------------------------

async def query_forked(
    prompt_messages: list[Any],
    can_use_tool: Callable[..., Any],
    query_source: str,
    fork_label: str,
    *,
    overrides: Any | None = None,
    max_output_tokens: int | None = None,
    max_turns: int | None = None,
    on_message: Callable[[Any], None] | None = None,
    skip_transcript: bool = False,
    skip_cache_write: bool = False,
    cache_safe_params: CacheSafeParams | None = None,
) -> ForkedAgentResult:
    """Run a forked agent using the most recently saved cache-safe params.

    This is a convenience wrapper that picks up the last cache-safe params
    saved by the main query loop (via save_cache_safe_params). If an explicit
    cache_safe_params argument is provided it takes precedence.

    Args:
        prompt_messages: Initial messages for the forked agent.
        can_use_tool: Permission check callback.
        query_source: Source identifier for analytics.
        fork_label: Label for analytics (e.g. 'session_memory', 'supervisor').
        overrides: Optional subagent context overrides.
        max_output_tokens: Optional cap on output tokens.
        max_turns: Optional cap on turn count.
        on_message: Optional per-message callback for streaming.
        skip_transcript: Skip sidechain transcript recording.
        skip_cache_write: Skip writing prompt cache entries.
        cache_safe_params: Explicit cache-safe params (overrides last saved).

    Returns:
        ForkedAgentResult with messages and total usage.

    Raises:
        RuntimeError: When no cache-safe params are available.
    """

    csp = cache_safe_params or get_last_cache_safe_params()
    if csp is None:
        raise RuntimeError(
            "No cache-safe params available. Ensure save_cache_safe_params() "
            "is called before query_forked(), or pass cache_safe_params explicitly."
        )

    params = ForkedAgentParams(
        prompt_messages=prompt_messages,
        cache_safe_params=csp,
        can_use_tool=can_use_tool,
        query_source=query_source,
        fork_label=fork_label,
        overrides=overrides,
        max_output_tokens=max_output_tokens,
        max_turns=max_turns,
        on_message=on_message,
        skip_transcript=skip_transcript,
        skip_cache_write=skip_cache_write,
    )

    return await run_forked_agent(params)


# ---------------------------------------------------------------------------
# Usage helpers
# ---------------------------------------------------------------------------

def empty_usage() -> dict[str, int]:
    """Return a zeroed usage dict with all standard fields."""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def accumulate_usage(
    current: dict[str, int],
    incoming: dict[str, int],
) -> dict[str, int]:
    """Accumulate incoming usage into the current total.

    Public proxy for _accumulate_usage.
    """
    return _accumulate_usage(current, incoming)


def total_cost_from_usage(
    usage: dict[str, int],
    input_cost_per_mtok: float = 0.0,
    output_cost_per_mtok: float = 0.0,
    cache_read_cost_per_mtok: float = 0.0,
    cache_write_cost_per_mtok: float = 0.0,
) -> float:
    """Estimate total cost from usage tokens and per-million-token prices.

    Args:
        usage: Usage dict with token counts.
        input_cost_per_mtok: Cost per million input tokens.
        output_cost_per_mtok: Cost per million output tokens.
        cache_read_cost_per_mtok: Cost per million cache-read tokens.
        cache_write_cost_per_mtok: Cost per million cache-write tokens.

    Returns:
        Estimated cost in USD.
    """

    return (
        usage.get("input_tokens", 0) * input_cost_per_mtok / 1_000_000
        + usage.get("output_tokens", 0) * output_cost_per_mtok / 1_000_000
        + usage.get("cache_read_input_tokens", 0)
        * cache_read_cost_per_mtok
        / 1_000_000
        + usage.get("cache_creation_input_tokens", 0)
        * cache_write_cost_per_mtok
        / 1_000_000
    )
