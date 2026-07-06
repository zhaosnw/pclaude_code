"""
Structured IO — full SDK protocol handler for NDJSON stdin/stdout messaging.

Port of: src/cli/structuredIO.ts

Provides:
- AsyncGenerator-based message reader from stdin (structuredInput)
- sendRequest() with pending request map and schema validation
- Permission request handling (can_use_tool, hook callback, sandbox, elicitation)
- Control protocol (control_request/response/cancel)
- Bridge support (injectControlResponse, onControlRequestSent/Resolved)
- History replay dedup via resolvedToolUseIds Set
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, AsyncIterable, Callable, Optional

from hare.cli.ndjson_safe_stringify import ndjson_safe_stringify

logger = logging.getLogger(__name__)

SANDBOX_NETWORK_ACCESS_TOOL_NAME = "SandboxNetworkAccess"
MAX_RESOLVED_TOOL_USE_IDS = 1000

# ---------------------------------------------------------------------------
# Permission / hook / elicitation types
# ---------------------------------------------------------------------------


@dataclass
class PermissionDecision:
    """Decision returned from the permission prompt flow."""

    behavior: str = "deny"  # "allow" | "deny" | "ask"
    updated_input: dict[str, Any] | None = None
    message: str = ""
    tool_use_id: str = ""
    decision_reason: dict[str, Any] | None = None
    updated_permissions: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, Any]] | None = None
    blocked_path: str | None = None


@dataclass
class HookCallback:
    """A hook callback that sends hook_callback control_requests to SDK host.

    Mirrors TS HookCallback type — used by the hook runner to call back
    into the SDK consumer (VS Code, CCR) for hook execution.
    """

    type: str = "callback"
    timeout: int | None = None
    callback: Callable[..., Any] | None = None


@dataclass
class HookInput:
    """Input passed to a hook callback."""

    hook_event_name: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = ""
    session_id: str = ""
    transcript_path: str = ""
    cwd: str = ""
    permission_mode: str = ""
    stop_reason: str = ""


@dataclass
class HookJSONOutput:
    """Structured output expected from a hook callback response."""

    decision: str = ""  # "approve" | "block" | ""
    reason: str = ""
    additional_context: str = ""
    continue_: bool = True
    stop_reason: str = ""
    permission_decision: str = ""
    updated_input: dict[str, Any] | None = None
    message: str = ""


@dataclass
class ElicitResult:
    """Result from an elicitation request to the SDK host."""

    action: str = "cancel"  # "accept" | "cancel"
    data: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helper: validate permission prompt tool result
# ---------------------------------------------------------------------------


def _validate_permission_output(data: dict[str, Any]) -> PermissionDecision:
    """Parse SDK permission response into a PermissionDecision.

    The SDK host responds with { behavior, updatedInput?, message?,
    toolUseID?, mode?, updatedPermissions? }.
    """
    behavior = data.get("behavior", "deny")
    if behavior not in ("allow", "deny", "ask"):
        behavior = "deny"

    return PermissionDecision(
        behavior=behavior,
        updated_input=data.get("updatedInput"),
        message=data.get("message", ""),
        tool_use_id=data.get("toolUseID", ""),
        updated_permissions=data.get("updatedPermissions") or data.get("updatedRules") or [],
        suggestions=data.get("permissionSuggestions", data.get("suggestions")),
        blocked_path=data.get("blockedPath"),
    )


def _validate_hook_json_output(data: dict[str, Any]) -> HookJSONOutput:
    """Parse hook callback response into HookJSONOutput."""
    return HookJSONOutput(
        decision=data.get("decision", ""),
        reason=data.get("reason", ""),
        additional_context=data.get("additionalContext", ""),
        continue_=data.get("continue", True),
        stop_reason=data.get("stopReason", ""),
        permission_decision=data.get("permissionDecision", ""),
        updated_input=data.get("updatedInput"),
        message=data.get("message", ""),
    )


def _validate_elicitation_response(data: dict[str, Any]) -> ElicitResult:
    """Parse SDK elicitation response into ElicitResult.

    The SDK host responds with { action, content? / data? }.
    Matches TS SDKControlElicitationResponseSchema.
    """
    action = data.get("action", "cancel")
    if action not in ("accept", "decline", "cancel"):
        action = "cancel"
    # Normalize: SDK can return 'decline' instead of 'cancel'
    if action == "decline":
        action = "cancel"
    return ElicitResult(
        action=action,
        data=data.get("content") or data.get("data"),
    )


# ---------------------------------------------------------------------------
# StructuredIO
# ---------------------------------------------------------------------------


class StructuredIO:
    """Provides structured read/write of SDK messages via stdio."""

    def __init__(
        self,
        input_stream: AsyncIterable[str] | Any,
        replay_user_messages: bool = False,
    ) -> None:
        self._input = input_stream
        self._replay_user_messages = replay_user_messages
        self._pending_requests: dict[str, PendingRequest] = {}
        self._input_closed = False
        self._unexpected_response_callback: Callable[..., Any] | None = None
        self._resolved_tool_use_ids: set[str] = set()
        self._prepended_lines: list[str] = []
        self._on_control_request_sent: Callable[..., Any] | None = None
        self._on_control_request_resolved: Callable[..., Any] | None = None

        # Public: async generator yielding parsed messages
        self.structured_input = self._read()

    # ---- Public API ----

    def prepend_user_message(self, content: str) -> None:
        """Queue a user turn to be yielded before next input."""
        msg = ndjson_safe_stringify(
            {
                "type": "user",
                "session_id": "",
                "message": {"role": "user", "content": content},
                "parent_tool_use_id": None,
            }
        )
        self._prepended_lines.append(msg + "\n")

    async def write(self, message: dict[str, Any]) -> None:
        """Write a StdoutMessage to stdout."""
        line = ndjson_safe_stringify(message)
        import sys

        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    async def send_request(
        self,
        request_type: str,
        data: dict[str, Any] | None = None,
        schema: Any = None,
    ) -> Any:
        """Send a control_request and await the response."""
        req_id = str(uuid.uuid4())
        request: dict[str, Any] = {
            "type": "control_request",
            "request_id": req_id,
            "request": {"subtype": request_type, **(data or {})},
        }
        if self._on_control_request_sent and request_type == "can_use_tool":
            self._on_control_request_sent(request)

        future: asyncio.Future[Any] = asyncio.Future()
        self._pending_requests[req_id] = PendingRequest(
            resolve=lambda r: future.set_result(r) if not future.done() else None,
            reject=lambda e: future.set_exception(e) if not future.done() else None,
            schema=schema,
            request=request,
        )

        await self.write(request)
        try:
            result = await future
        finally:
            self._pending_requests.pop(req_id, None)

        if schema and result is not None:
            try:
                return schema(result)  # Validate if schema is a callable
            except Exception as e:
                raise ValueError(f"Response validation failed: {e}")
        return result

    def inject_control_response(self, response: dict[str, Any]) -> None:
        """Inject a control_response from the bridge to resolve pending request."""
        req_id = response.get("response", {}).get("request_id")
        if not req_id:
            return
        pending = self._pending_requests.get(req_id)
        if not pending:
            return
        self._track_resolved_tool_use_id(pending.request)
        del self._pending_requests[req_id]
        # Cancel the SDK consumer's callback
        asyncio.ensure_future(
            self.write(
                {
                    "type": "control_cancel_request",
                    "request_id": req_id,
                }
            )
        )
        subtype = response.get("response", {}).get("subtype")
        if subtype == "error":
            pending.reject(
                RuntimeError(response.get("response", {}).get("error", "Unknown error"))
            )
        else:
            result = response.get("response", {}).get("response", {})
            try:
                if pending.schema and callable(pending.schema):
                    pending.resolve(pending.schema(result))
                else:
                    pending.resolve(result)
            except Exception as e:
                pending.reject(e)

    def cancel_request(self, request_id: str) -> bool:
        """Cancel a pending control_request by request_id.

        Sends control_cancel_request on stdout and rejects the pending future.
        Returns True if a request was found and cancelled.
        """
        pending = self._pending_requests.get(request_id)
        if pending is None:
            return False
        self._track_resolved_tool_use_id(pending.request)
        del self._pending_requests[request_id]
        # Send cancellation to host
        asyncio.ensure_future(
            self.write(
                {
                    "type": "control_cancel_request",
                    "request_id": request_id,
                }
            )
        )
        pending.reject(asyncio.CancelledError(f"Request {request_id} cancelled"))
        return True

    def set_unexpected_response_callback(self, cb: Callable[..., Any]) -> None:
        self._unexpected_response_callback = cb

    def set_on_control_request_sent(self, cb: Callable[..., Any] | None) -> None:
        self._on_control_request_sent = cb

    def set_on_control_request_resolved(self, cb: Callable[..., Any] | None) -> None:
        self._on_control_request_resolved = cb

    def get_pending_permission_requests(self) -> list[dict[str, Any]]:
        return [
            p.request
            for p in self._pending_requests.values()
            if p.request.get("request", {}).get("subtype") == "can_use_tool"
        ]

    def flush_internal_events(self) -> Any:
        return None  # Overridden by RemoteIO

    @property
    def internal_events_pending(self) -> int:
        return 0

    # ---- Hook callback support ----

    def create_hook_callback(
        self,
        callback_id: str,
        timeout: int | None = None,
    ) -> HookCallback:
        """Create a HookCallback that sends hook_callback control_requests.

        The returned callback is invoked by the hook runner; it sends a
        hook_callback control_request to the SDK host (VS Code, CCR) and
        awaits the structured response via the control protocol.

        Args:
            callback_id: Unique id used to route the hook at the host.
            timeout: Optional timeout in ms for the hook at the host side.
        """
        async def hook_callback_fn(
            input: HookInput | dict[str, Any],
            tool_use_id: str | None = None,
            abort_signal: Any = None,
        ) -> HookJSONOutput:
            """Send hook_callback request to SDK host and await response.

            Args:
                input: Hook input (HookInput dataclass or dict).
                tool_use_id: Tool use ID associated with this hook invocation.
                abort_signal: Optional AbortSignal (asyncio.Event or similar)
                    that cancels the request when set.

            Returns:
                HookJSONOutput with the hook's decision.
            """
            try:
                input_dict = (
                    input.__dict__
                    if isinstance(input, HookInput)
                    else (input if isinstance(input, dict) else {})
                )
                # If abort_signal is already set, bail out immediately
                if abort_signal is not None:
                    if hasattr(abort_signal, "is_set") and callable(
                        abort_signal.is_set
                    ):
                        if abort_signal.is_set():
                            return HookJSONOutput()
                    elif hasattr(abort_signal, "aborted") and abort_signal.aborted:
                        return HookJSONOutput()
                result = await self.send_request(
                    request_type="hook_callback",
                    data={
                        "callback_id": callback_id,
                        "input": input_dict,
                        "tool_use_id": tool_use_id or None,
                    },
                    schema=_validate_hook_json_output,
                )
                return result
            except asyncio.CancelledError:
                logger.debug(
                    "Hook callback %s cancelled (tool_use_id=%s)",
                    callback_id,
                    tool_use_id,
                )
                return HookJSONOutput()
            except Exception as exc:
                logger.warning(
                    "Hook callback %s failed: %s", callback_id, exc, exc_info=True
                )
                return HookJSONOutput()

        return HookCallback(
            type="callback",
            timeout=timeout,
            callback=hook_callback_fn,
        )

    # ---- Sandbox network access support ----

    def create_sandbox_ask_callback(self) -> Callable[..., Any]:
        """Create a callback for sandbox network permission requests.

        Forwards sandbox network access prompts to the SDK host as
        can_use_tool control_requests with the synthetic tool name
        SANDBOX_NETWORK_ACCESS_TOOL_NAME, so SDK hosts (VS Code, CCR)
        can prompt the user without a new protocol subtype.

        Returns an async callable (hostPattern) -> bool where hostPattern
        is { host: str, port?: int }.

        The callback piggybacks on the existing can_use_tool protocol so
        that SDK hosts can use their normal permission dialog flow for
        sandbox network access decisions.
        """
        async def sandbox_ask(host_pattern: dict[str, Any]) -> bool:
            """Ask SDK host whether to allow network access to host_pattern."""
            host = host_pattern.get("host", "")
            port = host_pattern.get("port")
            tool_use_id = str(uuid.uuid4())
            try:
                result = await self.send_request(
                    request_type="can_use_tool",
                    data={
                        "tool_name": SANDBOX_NETWORK_ACCESS_TOOL_NAME,
                        "input": {
                            "host": host,
                            **({"port": port} if port is not None else {}),
                        },
                        "tool_use_id": tool_use_id,
                        "description": (
                            f"Allow network connection to {host}"
                            + (f":{port}" if port is not None else "")
                            + "?"
                        ),
                    },
                    schema=_validate_permission_output,
                )
                return result.behavior == "allow"
            except asyncio.CancelledError:
                logger.debug(
                    "Sandbox network access request cancelled for %s (tool_use_id=%s)",
                    host,
                    tool_use_id,
                )
                return False
            except Exception as exc:
                logger.warning(
                    "Sandbox network access request failed for %s: %s",
                    host,
                    exc,
                )
                return False

        return sandbox_ask

    # ---- Elicitation support ----

    async def handle_elicitation(
        self,
        server_name: str,
        message: str,
        requested_schema: dict[str, Any] | None = None,
        mode: str | None = None,
        url: str | None = None,
        elicitation_id: str | None = None,
        abort_signal: Any = None,
    ) -> ElicitResult:
        """Send an elicitation request to the SDK consumer and return result.

        The SDK host (VS Code, CCR) presents the elicitation to the user
        and returns either 'accept' (with data) or 'decline'/'cancel'.

        Args:
            server_name: MCP server name requesting elicitation.
            message: Prompt message to show the user.
            requested_schema: JSON Schema for the expected response data.
            mode: 'form' or 'url' — presentation mode for the host.
            url: URL for url-mode elicitation.
            elicitation_id: Server-assigned id for matching with
                ElicitationResult hook event.
            abort_signal: Optional AbortSignal to cancel the request.
        """
        try:
            # Check abort before sending
            if abort_signal is not None:
                if hasattr(abort_signal, "is_set") and callable(
                    abort_signal.is_set
                ):
                    if abort_signal.is_set():
                        return ElicitResult(action="cancel")
                elif hasattr(abort_signal, "aborted") and abort_signal.aborted:
                    return ElicitResult(action="cancel")

            result = await self.send_request(
                request_type="elicitation",
                data={
                    "mcp_server_name": server_name,
                    "message": message,
                    "mode": mode,
                    "url": url,
                    "elicitation_id": elicitation_id,
                    "requested_schema": requested_schema,
                },
                schema=_validate_elicitation_response,
            )
            return result
        except asyncio.CancelledError:
            logger.debug(
                "Elicitation request cancelled (server=%s)", server_name
            )
            return ElicitResult(action="cancel")
        except Exception as exc:
            logger.warning(
                "Elicitation request failed for server %s: %s",
                server_name,
                exc,
            )
            return ElicitResult(action="cancel")

    # ---- MCP message support ----

    async def send_mcp_message(
        self,
        server_name: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Send an MCP JSON-RPC message to an SDK server and await response.

        Args:
            server_name: Name of the MCP server.
            message: JSON-RPC message to forward.

        Returns:
            The mcp_response field from the SDK host's response.
        """
        try:
            response = await self.send_request(
                request_type="mcp_message",
                data={
                    "server_name": server_name,
                    "message": message,
                },
                schema=None,
            )
            if isinstance(response, dict):
                return response.get("mcp_response", response)
            return {"error": "Invalid MCP response"}
        except Exception as exc:
            logger.warning(
                "MCP message to server %s failed: %s",
                server_name,
                exc,
            )
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": f"MCP message relay failed: {exc}",
                },
            }

    # ---- CanUseTool factory (permission request flow) ----

    def create_can_use_tool(
        self,
        on_permission_prompt: Callable[..., Any] | None = None,
    ) -> Callable[..., Any]:
        """Create a CanUseToolFn that performs the full SDK permission flow.

        The returned function:
        1. Checks whether the tool is already allowed/denied via static rules.
        2. If permission is needed ('ask'), sends a can_use_tool control_request
           to the SDK host and returns the user's decision.
        3. Handles aborts, cancellations, and errors gracefully.

        Args:
            on_permission_prompt: Optional callback invoked with prompt details
                before the control_request is sent. Receives a dict with
                tool_name, action_description, tool_use_id, request_id, input.

        Returns:
            Async callable (tool, input, tool_use_context, assistant_message,
            tool_use_id) -> PermissionDecision.
        """
        async def can_use_tool_fn(
            tool: Any,
            input: dict[str, Any],
            tool_use_context: Any,
            assistant_message: Any,
            tool_use_id: str,
            force_decision: PermissionDecision | None = None,
        ) -> PermissionDecision:
            """Determine whether the tool can be used, prompting if needed."""

            # If a forced decision is supplied, use it directly
            if force_decision is not None:
                return force_decision

            # Resolve description for the permission prompt
            description = _resolve_tool_description(tool, input)

            request_id = str(uuid.uuid4())
            prompt_details: dict[str, Any] = {
                "tool_name": getattr(tool, "name", "unknown"),
                "action_description": description,
                "tool_use_id": tool_use_id,
                "request_id": request_id,
                "input": input,
            }

            # Notify the host that a permission prompt is about to be sent
            if on_permission_prompt is not None:
                try:
                    on_permission_prompt(prompt_details)
                except Exception:
                    logger.debug(
                        "on_permission_prompt callback failed for %s",
                        tool_use_id,
                        exc_info=True,
                    )

            try:
                result = await self.send_request(
                    request_type="can_use_tool",
                    data={
                        "tool_name": getattr(tool, "name", "unknown"),
                        "input": input,
                        "tool_use_id": tool_use_id,
                        "permission_suggestions": None,
                        "blocked_path": None,
                        "decision_reason": None,
                    },
                    schema=_validate_permission_output,
                )
                return result
            except asyncio.CancelledError:
                logger.debug(
                    "can_use_tool request %s cancelled for tool '%s'",
                    request_id,
                    getattr(tool, "name", "unknown"),
                )
                return PermissionDecision(
                    behavior="deny",
                    message="Permission request was cancelled",
                    tool_use_id=tool_use_id,
                )
            except Exception as exc:
                logger.warning(
                    "can_use_tool request %s failed for tool '%s': %s",
                    request_id,
                    getattr(tool, "name", "unknown"),
                    exc,
                )
                return PermissionDecision(
                    behavior="deny",
                    message=f"Tool permission request failed: {exc}",
                    tool_use_id=tool_use_id,
                )

        return can_use_tool_fn

    # ---- Internal: track resolved tool_use IDs for dedup ----

    def _track_resolved_tool_use_id(self, request: dict[str, Any]) -> None:
        req = request.get("request", {})
        if req.get("subtype") == "can_use_tool":
            tool_id = req.get("tool_use_id")
            if tool_id:
                self._resolved_tool_use_ids.add(tool_id)
                if len(self._resolved_tool_use_ids) > MAX_RESOLVED_TOOL_USE_IDS:
                    first = next(iter(self._resolved_tool_use_ids))
                    self._resolved_tool_use_ids.discard(first)

    # ---- Internal: message reader (AsyncGenerator) ----

    async def _read(self) -> AsyncGenerator[dict[str, Any], None]:
        """Read NDJSON lines from input, parse, and yield messages."""
        content = ""

        async def _split_and_process() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal content
            while True:
                if self._prepended_lines:
                    content = "".join(self._prepended_lines) + content
                    self._prepended_lines = []
                newline = content.find("\n")
                if newline == -1:
                    break
                line = content[:newline]
                content = content[newline + 1 :]
                msg = await self._process_line(line)
                if msg is not None:
                    yield msg

        # Initial drain
        async for msg in _split_and_process():
            yield msg

        # Read from input
        async for block in self._iter_input():
            content += block
            async for msg in _split_and_process():
                yield msg

        # Final partial line
        if content:
            msg = await self._process_line(content)
            if msg is not None:
                yield msg

        self._input_closed = True
        for req in self._pending_requests.values():
            req.reject(
                RuntimeError("Tool permission stream closed before response received")
            )

    async def _iter_input(self) -> AsyncGenerator[str, None]:
        """Iterate over input stream."""
        if hasattr(self._input, "__aiter__"):
            async for chunk in self._input:
                yield chunk
        elif hasattr(self._input, "__iter__"):
            for line in self._input:
                yield line
        elif hasattr(self._input, "read"):
            # File-like object
            while True:
                chunk = self._input.read(8192)
                if not chunk:
                    break
                yield chunk if isinstance(chunk, str) else chunk.decode("utf-8")

    async def _process_line(self, line: str) -> dict[str, Any] | None:
        """Parse a single NDJSON line into a message.

        Handles all SDK stdin message types:
        - user: user messages
        - control_request: inbound control requests (interrupt, hook_callback,
          elicitation, sandbox, etc.)
        - control_response: responses to outbound control requests
        - assistant / system: replay messages
        - keep_alive: silently dropped
        - update_environment_variables: applied and dropped
        - Unknown types: logged and dropped (matching TS behavior)
        """
        if not line.strip():
            return None
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return None

        if not isinstance(msg, dict):
            return None

        msg_type = msg.get("type", "")

        # Silently drop keepalive
        if msg_type == "keep_alive":
            return None

        # Apply environment variable updates
        if msg_type == "update_environment_variables":
            import os

            for k, v in msg.get("variables", {}).items():
                os.environ[k] = str(v)
            return None

        # Handle control_response
        if msg_type == "control_response":
            resp = msg.get("response", {})
            req_id = resp.get("request_id", "")
            pending = self._pending_requests.get(req_id)

            if not pending:
                # Check if already resolved
                response_payload = (
                    resp.get("response", {})
                    if resp.get("subtype") == "success"
                    else None
                )
                tool_use_id = (
                    response_payload.get("toolUseID")
                    if isinstance(response_payload, dict)
                    else None
                )
                if tool_use_id and tool_use_id in self._resolved_tool_use_ids:
                    return None
                if self._unexpected_response_callback:
                    await self._unexpected_response_callback(msg)
                # Propagate control responses when replay is enabled
                if self._replay_user_messages:
                    return msg
                return None

            self._track_resolved_tool_use_id(pending.request)
            del self._pending_requests[req_id]

            # Notify the bridge when the SDK consumer resolves a can_use_tool
            # request, so it can cancel the stale permission prompt on claude.ai.
            if (
                pending.request.get("request", {}).get("subtype") == "can_use_tool"
                and self._on_control_request_resolved
            ):
                self._on_control_request_resolved(req_id)

            if resp.get("subtype") == "error":
                pending.reject(
                    RuntimeError(resp.get("error", "Control request failed"))
                )
            else:
                result = resp.get("response", {})
                pending.resolve(result)

            # Propagate control responses when replay is enabled
            if self._replay_user_messages:
                return msg
            return None

        # Validate and pass through known message types.
        # control_request messages must have a valid request field.
        if msg_type == "control_request":
            if not msg.get("request"):
                logger.error("control_request missing request field, dropping")
                return None
            return msg

        # Only allow these message types through (TS exact match).
        if msg_type in ("user", "assistant", "system"):
            return msg

        # Unknown message type — drop with warning (TS behavior).
        logger.warning("Ignoring unknown message type: %s", msg_type)
        return None


# ---------------------------------------------------------------------------
# PendingRequest
# ---------------------------------------------------------------------------


class PendingRequest:
    def __init__(
        self,
        resolve: Callable[..., None],
        reject: Callable[..., None],
        schema: Any,
        request: dict[str, Any],
    ) -> None:
        self.resolve = resolve
        self.reject = reject
        self.schema = schema
        self.request = request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_tool_description(tool: Any, input: dict[str, Any]) -> str:
    """Resolve a human-readable description for a tool's permission prompt.

    Tries per-tool description methods, falling back to the tool name.
    Mirrors TS buildRequiresActionDetails logic.
    """
    try:
        # Try tool.getActivityDescription(input) first
        fn = getattr(tool, "getActivityDescription", None)
        if callable(fn):
            result = fn(input)
            if isinstance(result, str) and result:
                return result

        # Fall back to getToolUseSummary
        fn = getattr(tool, "getToolUseSummary", None)
        if callable(fn):
            result = fn(input)
            if isinstance(result, str) and result:
                return result

        # Try userFacingName
        fn = getattr(tool, "userFacingName", None)
        if callable(fn):
            result = fn(input)
            if isinstance(result, str) and result:
                return result
    except Exception:
        logger.debug("Failed to resolve tool description", exc_info=True)

    # Ultimate fallback
    return getattr(tool, "name", "unknown tool")
