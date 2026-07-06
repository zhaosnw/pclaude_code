"""
MCP elicitation request handling (URL / form flows).

Port of: src/services/mcp/elicitationHandler.ts

Handles elicitation/create requests and elicitation/complete notifications from MCP
servers. Supports form mode (structured JSON Schema input) and URL mode (navigate to
external URL). Maintains an in-memory queue and integrates with the hook system.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from hare.utils.hooks import _get_matching_hooks, _run_single_hook

logger = logging.getLogger(__name__)

ElicitationMode = Literal["form", "url"]
ElicitationAction = Literal["accept", "decline", "cancel"]
WaitingDismissAction = Literal["dismiss", "retry", "cancel"]
ELICITATION_CREATE = "elicitation/create"
ELICITATION_COMPLETE = "notifications/elicitation/complete"


@dataclass
class ElicitationWaitingState:
    action_label: str
    show_cancel: bool = False


@dataclass
class ElicitResult:
    action: ElicitationAction
    content: dict[str, Any] | None = None


@dataclass
class ElicitationRequestEvent:
    server_name: str
    request_id: str | int
    params: dict[str, Any]
    respond: Callable[[ElicitResult], None]
    waiting_state: Optional[ElicitationWaitingState] = None
    on_waiting_dismiss: Optional[Callable[[WaitingDismissAction], None]] = None
    completed: bool = False


# ---------------------------------------------------------------------------
# In-memory queue
# ---------------------------------------------------------------------------

_elicitation_queue: list[ElicitationRequestEvent] = []
_queue_lock: asyncio.Lock = asyncio.Lock()


def get_elicitation_queue() -> list[ElicitationRequestEvent]:
    return list(_elicitation_queue)


def _mode(params: dict[str, Any]) -> ElicitationMode:
    return "url" if params.get("mode") == "url" else "form"


async def dismiss_elicitation(
    server_name: str, request_id: str | int, action: ElicitationAction,
) -> ElicitResult | None:
    """Remove from queue and invoke respond callback. Returns result or None."""
    r = ElicitResult(action=action)
    async with _queue_lock:
        for i, e in enumerate(_elicitation_queue):
            if e.server_name == server_name and e.request_id == request_id:
                _elicitation_queue.pop(i)
                try:
                    e.respond(r)
                except Exception:
                    logger.exception("respond failed: %s", server_name)
                return r
    return None


async def clear_elicitation_queue() -> None:
    """Cancel all pending elicitations."""
    async with _queue_lock:
        events = list(_elicitation_queue)
        _elicitation_queue.clear()
    for e in events:
        try:
            e.respond(ElicitResult(action="cancel"))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hook execution
# ---------------------------------------------------------------------------


async def run_elicitation_hooks(
    server_name: str, params: dict[str, Any], signal: Any = None,
) -> ElicitResult | None:
    """Execute Elicitation hooks. Returns ElicitResult if programmatically resolved."""
    handlers = _get_matching_hooks("Elicitation")
    if not handlers:
        return None
    m = _mode(params)
    ctx = dict(server_name=server_name, message=params.get("message", ""), mode=m,
               signal=signal, requestedSchema=params.get("requestedSchema") if m == "form" else None,
               url=params.get("url") if m == "url" else None,
               elicitationId=params.get("elicitationId") if m == "url" else None)
    for r in await asyncio.gather(*[_run_single_hook(h, ctx) for h in handlers],
                                   return_exceptions=True):
        if not isinstance(r, dict):
            continue
        if r.get("blockingError"):
            return ElicitResult(action="decline")
        resp = r.get("elicitationResponse")
        if isinstance(resp, dict):
            return ElicitResult(action=resp.get("action", "accept"), content=resp.get("content"))
    return None


async def run_elicitation_result_hooks(
    server_name: str, result: ElicitResult, signal: Any = None,
    mode: ElicitationMode | None = None, elicitation_id: str | None = None,
) -> ElicitResult:
    """Execute ElicitationResult hooks; return (possibly overridden) result."""
    handlers = _get_matching_hooks("ElicitationResult")
    final = result
    if handlers:
        ctx = dict(server_name=server_name, action=result.action, content=result.content,
                   mode=mode, elicitationId=elicitation_id, signal=signal)
        for r in await asyncio.gather(*[_run_single_hook(h, ctx) for h in handlers],
                                       return_exceptions=True):
            if not isinstance(r, dict):
                continue
            if r.get("blockingError"):
                final = ElicitResult(action="decline")
                break
            hr = r.get("elicitationResultResponse")
            if isinstance(hr, dict):
                final = ElicitResult(action=hr.get("action", result.action),
                                     content=hr.get("content", result.content))
    # Fire-and-forget notification hook for observability
    nh = _get_matching_hooks("Notification")
    if nh:
        ctx = dict(message=f'Elicitation response for server "{server_name}": {final.action}',
                   notification_type="elicitation_response", server_name=server_name,
                   action=final.action, elicitationId=elicitation_id)
        asyncio.ensure_future(
            asyncio.gather(*[_run_single_hook(h, ctx) for h in nh], return_exceptions=True))
    return final


# ---------------------------------------------------------------------------
# Client registration
# ---------------------------------------------------------------------------


def _extract_params(obj: Any) -> dict[str, Any]:
    p = obj.get("params", {}) if isinstance(obj, dict) else getattr(obj, "params", {})
    if hasattr(p, "__dict__") and not isinstance(p, dict):
        return vars(p)
    return p if isinstance(p, dict) else {}


def _reg(client: Any, method: str, handler: Callable[..., Any], *, notif: bool = False) -> None:
    fn = "setNotificationHandler" if notif else "setRequestHandler"
    key = {"method": method}
    try:
        getattr(client, fn)(key, handler)
    except Exception:
        alt = "set_notification_handler" if notif else "set_request_handler"
        if hasattr(client, alt):
            getattr(client, alt)(method, handler)
        elif hasattr(client, "on"):
            client.on(f"{'notification' if notif else 'request'}:{method}", handler)
        else:
            raise


def register_elicitation_handler(
    client: Any, server_name: str, on_queue_change: Callable[[], None] | None = None,
) -> bool:
    """Register request handler for elicitation/create + notification handler for
    elicitation/complete. Returns True on success, False if client lacks capability."""
    if not hasattr(client, "setRequestHandler"):
        return False
    try:
        _wire(client, server_name, on_queue_change)
        return True
    except Exception:
        logger.warning("Elicitation registration failed for '%s'", server_name)
        return False


def _wire(client: Any, server_name: str, on_change: Callable[[], None] | None) -> None:

    async def _on_request(request: Any, extra: Any) -> dict[str, Any]:
        pr = _extract_params(request)
        signal = getattr(extra, "signal", None) if extra else None
        try:
            hr = await run_elicitation_hooks(server_name, pr, signal)
            if hr is not None:
                return {"action": hr.action, "content": hr.content}
            # Queue for user interaction
            rid = getattr(extra, "requestId", 0) if extra else 0
            fut: asyncio.Future[ElicitResult] = asyncio.get_running_loop().create_future()

            def _abort() -> None:
                if not fut.done():
                    fut.set_result(ElicitResult(action="cancel"))
            if signal is not None:
                if getattr(signal, "aborted", False):
                    return {"action": "cancel"}
                if hasattr(signal, "add_event_listener"):
                    signal.add_event_listener("abort", _abort)

            def respond(r: ElicitResult) -> None:
                if not fut.done():
                    fut.set_result(r)
                if signal and hasattr(signal, "remove_event_listener"):
                    try:
                        signal.remove_event_listener("abort", _abort)
                    except Exception:
                        pass

            ws = ElicitationWaitingState(action_label="Skip confirmation") if (
                _mode(pr) == "url" and pr.get("elicitationId")) else None
            async with _queue_lock:
                _elicitation_queue.append(ElicitationRequestEvent(
                    server_name=server_name, request_id=rid, params=pr,
                    respond=respond, waiting_state=ws))
            if on_change:
                try:
                    on_change()
                except Exception:
                    pass
            resolved = await fut
            m = _mode(pr)
            final = await run_elicitation_result_hooks(
                server_name, resolved, signal, mode=m,
                elicitation_id=pr.get("elicitationId") if m == "url" else None)
            return {"action": final.action, "content": final.content}
        except asyncio.CancelledError:
            return {"action": "cancel"}
        except Exception:
            logger.exception("Elicitation handler error: %s", server_name)
            return {"action": "cancel"}

    async def _on_complete(notification: Any) -> None:
        params = _extract_params(notification)
        eid = params.get("elicitationId", "")
        if not eid:
            return
        nh = _get_matching_hooks("Notification")
        if nh:
            ctx = dict(message=f'MCP server "{server_name}" confirmed elicitation {eid} complete',
                       notification_type="elicitation_complete", server_name=server_name,
                       elicitationId=eid)
            asyncio.ensure_future(
                asyncio.gather(*[_run_single_hook(h, ctx) for h in nh], return_exceptions=True))
        async with _queue_lock:
            for e in _elicitation_queue:
                if (e.server_name == server_name and e.params.get("mode") == "url"
                        and e.params.get("elicitationId") == eid):
                    e.completed = True
                    break
        if on_change:
            try:
                on_change()
            except Exception:
                pass

    _reg(client, ELICITATION_CREATE, _on_request)
    _reg(client, ELICITATION_COMPLETE, _on_complete, notif=True)


# ---------------------------------------------------------------------------
# Legacy compat
# ---------------------------------------------------------------------------

def register_elicitation_handler_stub(client: Any, server_name: str) -> None:
    register_elicitation_handler(client, server_name)
