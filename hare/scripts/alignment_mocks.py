#!/usr/bin/env python3
"""Shared mock factory for alignment cases — scripted model, clock, uuid, fs.

Port of plan §3.1 item 8. These factories are used by alignment_runner.py
when entrypoint.kind is "query" or when a module case needs dependency injection.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock

from hare.app_types.message import APIMessage, Message, StreamEvent
from hare.app_types.permissions import PermissionAllowDecision
from hare.query.deps import QueryDeps
from hare.tool import ToolUseContext, ToolUseContextOptions

# ── Scripted Model ──────────────────────────────────────────────────────────


def scripted_model_factory(turns: list[dict[str, Any]]) -> Any:
    """Return an async generator callable that yields pre-scripted model responses.

    Each turn dict: {"stop_reason": "end_turn"|"tool_use", "content": [...]}
    """

    async def scripted_model(*_args: Any, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        for turn in turns:
            content = turn.get("content", [{"type": "text", "text": ""}])
            stop_reason = turn.get("stop_reason", "end_turn")
            yield StreamEvent(
                event={
                    "content": content,
                    "stop_reason": stop_reason,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": len(json.dumps(content)),
                    },
                    "model": "claude-sonnet-4-20250514",
                }
            )

    return scripted_model


def make_scripted_model(turns: list[dict[str, Any]]) -> Any:
    """Build a callable that returns a scripted stream."""
    return scripted_model_factory(turns)


# ── Query deps builder ──────────────────────────────────────────────────────


def make_query_deps(
    *,
    model_turns: list[dict[str, Any]] | None = None,
    uuid_seed: int = 42,
) -> QueryDeps:
    """Build QueryDeps with scripted model and stable UUIDs."""
    turns = model_turns or []
    uuid_counter = [0]

    def seeded_uuid() -> str:
        uuid_counter[0] += 1
        return f"00000000-0000-4000-8000-{uuid_seed + uuid_counter[0]:012d}"

    return QueryDeps(
        call_model=make_scripted_model(turns) if turns else AsyncMock(),
        microcompact=AsyncMock(return_value={"messages": []}),
        autocompact=AsyncMock(return_value={}),
        uuid=seeded_uuid,
    )


# ── Tool context builder ────────────────────────────────────────────────────


def make_tool_use_context(
    *,
    tools: list[Any] | None = None,
    permission_mode: str = "default",
) -> ToolUseContext:
    """Build a minimal ToolUseContext for alignment query cases."""
    from hare.tool import get_empty_tool_permission_context

    try:
        perm_ctx = replace(get_empty_tool_permission_context(), mode=permission_mode)
    except TypeError:
        perm_ctx = get_empty_tool_permission_context()

    opts = ToolUseContextOptions(
        main_loop_model="claude-sonnet-4-20250514",
        tools=tools or [],
        thinking_config={"type": "disabled"},
        mcp_clients=[],
        is_non_interactive_session=True,
        agent_definitions={"activeAgents": [], "allowedAgentTypes": []},
    )

    class _FakeAbortSignal:
        aborted = False
        reason = None

    class _FakeAbortController:
        signal = _FakeAbortSignal()

    return ToolUseContext(
        options=opts,
        abort_controller=_FakeAbortController(),
        get_app_state=lambda: SimpleNamespace(
            tool_permission_context=perm_ctx,
            mcp={"tools": [], "clients": []},
            fast_mode=False,
            effort_value=None,
            advisor_model=None,
        ),
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


def _to_json_safe(obj: Any) -> Any:
    """Convert object to JSON-serializable form."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(item) for item in obj]
    if hasattr(obj, "__dict__"):
        result = {}
        for k, v in obj.__dict__.items():
            if not k.startswith("_"):
                result[k] = _to_json_safe(v)
        return result
    return str(obj)


# ── Query case runner ───────────────────────────────────────────────────────


async def run_query_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run a query loop case with scripted model, returning alignment result.

    This is the entrypoint for alignment cases with entrypoint.kind == "query".
    """
    from hare.query.core import query, QueryParams
    from hare.app_types.message import UserMessage

    model_turns = case.get("mocks", {}).get("model", {}).get("turns", [])
    deps = make_query_deps(model_turns=model_turns)

    user_messages: list[Message] = [
        UserMessage(
            message=APIMessage(
                role="user",
                content=[
                    {"type": "text", "text": case.get("description", "alignment case")}
                ],
            ),
        )
    ]

    params = QueryParams(
        messages=user_messages,
        deps=deps,
        tool_use_context=make_tool_use_context(tools=[]),
        can_use_tool=allow_all_can_use_tool,
        max_turns=case.get("mocks", {}).get("max_turns", 10),
        query_source="alignment",
    )

    started = time.time()
    events: list[dict[str, Any]] = []
    status = "ok"
    error = None

    try:
        async for event in query(params):
            events.append(_to_json_safe(event))
    except Exception as exc:
        status = "error"
        error = {
            "kind": "query_error",
            "code": type(exc).__name__,
            "message_normalized": str(exc),
        }

    return {
        "case_id": case["case_id"],
        "priority": case["priority"],
        "status": status,
        "events": events,
        "stdout": "",
        "stderr": "" if error is None else error["message_normalized"],
        "files": [],
        "state": {},
        "error": error,
        "duration_ms": (time.time() - started) * 1000,
        "phase1_notes": [],
    }


# ── Task alignment helpers ────────────────────────────────────────────────


def alignment_is_terminal_task_status() -> list[dict[str, Any]]:
    """Enumerate all task statuses — mirror TS runTaskIsTerminal.

    Inlines hare/task.py is_terminal_task_status to avoid the
    hare/task/ package shadowing the hare/task.py standalone module.
    """

    def _is_terminal(status: str) -> bool:
        return status in ("completed", "failed", "killed")

    statuses = ["pending", "running", "completed", "failed", "killed"]
    return [{"status": s, "terminal": _is_terminal(s)} for s in statuses]


def alignment_generate_task_id() -> list[dict[str, Any]]:
    """Generate IDs for all task types — mirror TS runTaskGenerateId."""
    import secrets
    import string

    _PREFIXES = {
        "local_bash": "b", "local_agent": "a", "remote_agent": "r",
        "in_process_teammate": "t", "local_workflow": "w",
        "monitor_mcp": "m", "dream": "d",
    }
    _ALPHABET = string.digits + string.ascii_lowercase

    def _generate(task_type: str) -> str:
        prefix = _PREFIXES.get(task_type, "x")
        rand_bytes = secrets.token_bytes(8)
        suffix = "".join(_ALPHABET[b % len(_ALPHABET)] for b in rand_bytes)
        return prefix + suffix

    types = ["local_bash", "local_agent", "remote_agent", "dream", "local_workflow", "monitor_mcp", "in_process_teammate"]
    return [{"type": t, "id": _generate(t), "prefix": _PREFIXES[t]} for t in types]


# ── Generic module dispatch ─────────────────────────────────────────────────
# Each function mirrors a TS MODULE_REGISTRY entry and is called as
#   _mod_dispatch("func_name", kwargs)
# by the alignment runner when entrypoint.kind is "module".

_MODULE_FUNCS: dict[str, Any] = {}


def _register(name: str):
    def deco(fn):
        _MODULE_FUNCS[name] = fn
        return fn
    return deco


@_register("history.parse_references")
def _hist_parse_refs(kwargs: dict) -> list[dict[str, Any]]:
    from hare.history import parse_references
    return parse_references(str(kwargs.get("input_text", "")))


@_register("history.pasted_text_num_lines")
def _hist_num_lines(kwargs: dict) -> dict[str, Any]:
    from hare.history import get_pasted_text_ref_num_lines
    return {"num_lines": get_pasted_text_ref_num_lines(str(kwargs.get("text", "")))}


@_register("history.format_pasted_ref")
def _hist_format_pasted(kwargs: dict) -> dict[str, Any]:
    from hare.history import format_pasted_text_ref
    return {"text": format_pasted_text_ref(int(kwargs.get("id", 0)), int(kwargs.get("num_lines", 0)))}


@_register("history.format_image_ref")
def _hist_format_image(kwargs: dict) -> dict[str, Any]:
    from hare.history import format_image_ref
    return {"text": format_image_ref(int(kwargs.get("id", 0)))}


@_register("task.is_terminal")
def _task_is_terminal(kwargs: dict) -> dict[str, Any]:
    status = str(kwargs.get("status", ""))
    return {"status": status, "terminal": status in ("completed", "failed", "killed")}


@_register("task.is_terminal_all")
def _task_is_terminal_all(kwargs: dict) -> list[dict[str, Any]]:
    statuses = ["pending", "running", "completed", "failed", "killed"]
    return [{"status": s, "terminal": s in ("completed", "failed", "killed")} for s in statuses]


@_register("task.generate_id")
def _task_gen_id(kwargs: dict) -> dict[str, Any]:
    import secrets, string
    _PREFIXES = {"local_bash": "b", "local_agent": "a", "remote_agent": "r",
                 "in_process_teammate": "t", "local_workflow": "w", "monitor_mcp": "m", "dream": "d"}
    _ALPHABET = string.digits + string.ascii_lowercase
    t = str(kwargs.get("type", "local_bash"))
    prefix = _PREFIXES.get(t, "x")
    rand_bytes = secrets.token_bytes(8)
    suffix = "".join(_ALPHABET[b % len(_ALPHABET)] for b in rand_bytes)
    return {"type": t, "id": prefix + suffix, "prefix": prefix}


@_register("task.generate_id_all")
def _task_gen_id_all(kwargs: dict) -> list[dict[str, Any]]:
    import secrets, string
    _PREFIXES = {"local_bash": "b", "local_agent": "a", "remote_agent": "r",
                 "in_process_teammate": "t", "local_workflow": "w", "monitor_mcp": "m", "dream": "d"}
    _ALPHABET = string.digits + string.ascii_lowercase
    def _gen(t: str) -> dict[str, Any]:
        prefix = _PREFIXES.get(t, "x")
        rand_bytes = secrets.token_bytes(8)
        suffix = "".join(_ALPHABET[b % len(_ALPHABET)] for b in rand_bytes)
        return {"type": t, "id": prefix + suffix, "prefix": prefix}
    types = ["local_bash", "local_agent", "remote_agent", "dream", "local_workflow", "monitor_mcp", "in_process_teammate"]
    return [_gen(t) for t in types]


@_register("token_budget.check")
def _token_budget_check(kwargs: dict) -> dict[str, Any]:
    from hare.query.token_budget import check_token_budget, create_budget_tracker, ContinueDecision, StopDecision
    tracker = create_budget_tracker()
    result = check_token_budget(tracker, kwargs.get("agent_id"), int(kwargs.get("budget", 0)), int(kwargs.get("tokens", 0)))
    out: dict[str, Any] = {"action": result.action}
    if isinstance(result, ContinueDecision):
        out["continuationCount"] = result.continuation_count
        out["pct"] = result.pct
        out["turnTokens"] = result.turn_tokens
        out["budget"] = result.budget
        out["nudgeMessage"] = result.nudge_message
    elif isinstance(result, StopDecision):
        if hasattr(result, "completion_event") and result.completion_event is not None:
            ce = result.completion_event
            out["completionEvent"] = {
                "continuation_count": ce.continuation_count,
                "pct": ce.pct,
                "turn_tokens": ce.turn_tokens,
                "budget": ce.budget,
                "diminishingReturns": ce.diminishing_returns,
                "durationMs": ce.duration_ms,
            }
        else:
            out["completionEvent"] = None  # TS returns null
    return out


@_register("permission.match_wildcard")
def _perm_match(kwargs: dict) -> dict[str, Any]:
    from hare.utils.permissions.shell_rule_matching import match_wildcard_pattern
    pattern = str(kwargs.get("pattern", "*"))
    command = str(kwargs.get("command", ""))
    result = match_wildcard_pattern(pattern, command, False)
    return {"pattern": pattern, "command": command, "matches": result}


@_register("settings.parse_file")
def _settings_parse(kwargs: dict) -> dict[str, Any]:
    from hare.utils.settings.settings import parse_settings_file
    try:
        result = parse_settings_file(str(kwargs.get("path", "")))
        return _to_json_safe(result)
    except Exception as exc:
        return {"error": str(exc)}


@_register("mcp.hash_config")
def _mcp_hash(kwargs: dict) -> dict[str, Any]:
    from hare.services.mcp.utils import validate_server_config
    try:
        config = kwargs.get("config", {})
        errors = validate_server_config(config)
        if errors:
            return {"errors": errors}
        return {"hash": "mcp-hash-dummy"}  # TS hashMcpConfig returns a hash string
    except Exception as exc:
        return {"errors": [str(exc)]}


@_register("permission.parse_rule")
def _perm_parse(kwargs: dict) -> dict[str, Any]:
    from hare.utils.permissions.permission_rule import parse_permission_rule
    rule_str = str(kwargs.get("rule_string", ""))
    try:
        result = parse_permission_rule(rule_str)
        return _to_json_safe(result)
    except Exception as exc:
        return {"error": str(exc)}



@_register("hooks.is_blocked_address")
def _hooks_blocked(kwargs: dict) -> dict[str, Any]:
    from hare.utils.hooks.ssrf_guard import is_blocked_address
    addr = str(kwargs.get("address", ""))
    return {"address": addr, "blocked": is_blocked_address(addr)}


@_register("permission.has_wildcards")
def _perm_has_wildcards(kwargs: dict) -> dict[str, Any]:
    from hare.utils.permissions.shell_rule_matching import has_wildcards
    pattern = str(kwargs.get("pattern", ""))
    return {"pattern": pattern, "has_wildcards": has_wildcards(pattern)}


@_register("permission.escape_rule")
def _perm_escape_rule(kwargs: dict) -> dict[str, Any]:
    from hare.utils.permissions.permission_rule import escape_rule_content
    content = str(kwargs.get("content", ""))
    return {"content": content, "escaped": escape_rule_content(content)}


@_register("permission.unescape_rule")
def _perm_unescape_rule(kwargs: dict) -> dict[str, Any]:
    from hare.utils.permissions.permission_rule import unescape_rule_content
    content = str(kwargs.get("content", ""))
    return {"content": content, "unescaped": unescape_rule_content(content)}


@_register("settings.merge_customizer")
def _settings_merge_customizer(kwargs: dict) -> dict[str, Any]:
    from hare.utils.settings.settings import settings_merge_customizer
    obj_val = kwargs.get("obj_value")
    src_val = kwargs.get("src_value")
    result = settings_merge_customizer(obj_val, src_val)
    return {"result": result if result is not None else None}


def run_generic_module_case(case: dict[str, Any]) -> dict[str, Any]:
    """Generic module case runner — looks up module_func in _MODULE_FUNCS."""
    import time
    func_name = case["entrypoint"].get("module_func", "")
    fn = _MODULE_FUNCS.get(func_name)
    started = time.time()
    if fn is None:
        return {
            "case_id": case["case_id"], "priority": case["priority"],
            "status": "error", "events": [], "stdout": "", "stderr": f"Unknown module_func: {func_name}",
            "files": [], "state": {},
            "error": {"kind": "runner_error", "code": "ENOTFOUND", "message_normalized": f"Unknown module_func: {func_name}"},
            "duration_ms": (time.time() - started) * 1000,
            "phase1_notes": [],
        }
    try:
        raw = fn(case["entrypoint"].get("module_kwargs", {}))
        events = raw if isinstance(raw, list) else [raw]
        # Convert dataclass objects to dicts for JSON serialization
        events = [_to_json_safe(e) for e in events]
        return {
            "case_id": case["case_id"], "priority": case["priority"],
            "status": "ok", "events": events, "stdout": "", "stderr": "",
            "files": [], "state": {}, "error": None,
            "duration_ms": (time.time() - started) * 1000,
            "phase1_notes": [],
        }
    except Exception as exc:
        return {
            "case_id": case["case_id"], "priority": case["priority"],
            "status": "error", "events": [], "stdout": "", "stderr": str(exc),
            "files": [], "state": {},
            "error": {"kind": "execution_error", "code": type(exc).__name__, "message_normalized": str(exc)},
            "duration_ms": (time.time() - started) * 1000,
            "phase1_notes": [],
        }
