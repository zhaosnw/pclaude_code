"""
Async-local agent context for analytics (`agentContext.ts`).

Python uses :class:`contextvars.ContextVar` instead of Node ``AsyncLocalStorage``.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Callable, Literal, TypeVar, TypedDict

from hare.utils.agent_swarms_enabled import is_agent_swarms_enabled

T = TypeVar("T")


class SubagentContext(TypedDict, total=False):
    agent_id: str
    parent_session_id: str | None
    agent_type: Literal["subagent"]
    subagent_name: str | None
    is_built_in: bool | None
    invoking_request_id: str | None
    invocation_kind: Literal["spawn", "resume"] | None
    invocation_emitted: bool | None


class TeammateAgentContext(TypedDict, total=False):
    agent_id: str
    agent_name: str
    team_name: str
    agent_color: str | None
    plan_mode_required: bool
    parent_session_id: str
    is_team_lead: bool
    agent_type: Literal["teammate"]
    invoking_request_id: str | None
    invocation_kind: Literal["spawn", "resume"] | None
    invocation_emitted: bool | None


AgentContext = SubagentContext | TeammateAgentContext

_agent_ctx: ContextVar[AgentContext | None] = ContextVar("agent_context", default=None)


def get_agent_context() -> AgentContext | None:
    return _agent_ctx.get()


def run_with_agent_context(context: AgentContext, fn: Callable[[], T]) -> T:
    """Run *fn* with *context* set for this contextvar chain."""
    token = _agent_ctx.set(context)
    try:
        return fn()
    finally:
        _agent_ctx.reset(token)


def is_subagent_context(context: AgentContext | None) -> bool:
    return context is not None and context.get("agent_type") == "subagent"


def is_teammate_agent_context(context: AgentContext | None) -> bool:
    if not is_agent_swarms_enabled():
        return False
    return context is not None and context.get("agent_type") == "teammate"


def get_subagent_log_name() -> str | None:
    ctx = get_agent_context()
    if not is_subagent_context(ctx) or not ctx.get("subagent_name"):
        return None
    name = ctx["subagent_name"]
    assert name is not None
    return name if ctx.get("is_built_in") else "user-defined"


def consume_invoking_request_id() -> dict[str, object] | None:
    ctx = get_agent_context()
    if ctx is None:
        return None
    rid = ctx.get("invoking_request_id")
    if not rid or ctx.get("invocation_emitted"):
        return None
    ctx["invocation_emitted"] = True
    return {"invokingRequestId": rid, "invocationKind": ctx.get("invocation_kind")}
