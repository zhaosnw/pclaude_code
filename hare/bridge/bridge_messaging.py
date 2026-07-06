"""
Bridge messaging — ingress parsing, title extraction, control handling, echo dedup.

Port of: src/bridge/bridgeMessaging.ts

Shared transport-layer helpers for both env-based core (initBridgeCore)
and env-less core (initEnvLessBridgeCore).
"""

from __future__ import annotations

import json as _json
import uuid as _uuid
from typing import Any, Optional


# ─── Type guards ─────────────────────────────────────────────────────────


def is_sdk_message(value: Any) -> bool:
    return (
        value is not None
        and isinstance(value, dict)
        and "type" in value
        and isinstance(value["type"], str)
    )


def is_sdk_control_response(value: Any) -> bool:
    return (
        value is not None
        and isinstance(value, dict)
        and value.get("type") == "control_response"
        and "response" in value
    )


def is_sdk_control_request(value: Any) -> bool:
    return (
        value is not None
        and isinstance(value, dict)
        and value.get("type") == "control_request"
        and "request_id" in value
        and "request" in value
    )


def is_eligible_bridge_message(msg: dict[str, Any]) -> bool:
    """True for message types forwarded to the bridge transport."""
    msg_type = msg.get("type", "")
    if (msg_type in ("user", "assistant")) and msg.get("isVirtual"):
        return False
    return (
        msg_type == "user"
        or msg_type == "assistant"
        or (msg_type == "system" and msg.get("subtype") == "local_command")
    )


def extract_title_text(msg: dict[str, Any]) -> Optional[str]:
    """Extract title-worthy text from a Message for session naming."""
    if msg.get("type") != "user":
        return None
    if msg.get("isMeta") or msg.get("toolUseResult") or msg.get("isCompactSummary"):
        return None
    origin = msg.get("origin")
    if origin and origin.get("kind") != "human":
        return None

    content = msg.get("message", {}).get("content", "")
    raw: Optional[str] = None
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block.get("text", "")
                break

    if not raw:
        return None
    # Strip display tags
    import re

    clean = re.sub(r"<[^>]+>", "", raw).strip()
    return clean or None


# ─── Ingress routing ──────────────────────────────────────────────────────


def handle_ingress_message(
    data: str,
    recent_posted_uuids: BoundedUUIDSet,
    recent_inbound_uuids: BoundedUUIDSet,
    on_inbound_message: Any = None,
    on_permission_response: Any = None,
    on_control_request: Any = None,
) -> None:
    """Parse an ingress message and route to the appropriate handler."""
    try:
        parsed = _json.loads(data)
    except (_json.JSONDecodeError, TypeError):
        return

    if not isinstance(parsed, dict):
        return

    # control_response is not an SDKMessage — check first
    if is_sdk_control_response(parsed):
        if on_permission_response:
            on_permission_response(parsed)
        return

    # control_request from the server
    if is_sdk_control_request(parsed):
        if on_control_request:
            on_control_request(parsed)
        return

    if not is_sdk_message(parsed):
        return

    # Echo dedup
    msg_uuid = parsed.get("uuid") if isinstance(parsed.get("uuid"), str) else None

    if msg_uuid and msg_uuid in recent_posted_uuids:
        return  # echo of our own message

    if msg_uuid and msg_uuid in recent_inbound_uuids:
        return  # re-delivered inbound

    if parsed.get("type") == "user":
        if msg_uuid:
            recent_inbound_uuids.add(msg_uuid)
        if on_inbound_message:
            on_inbound_message(parsed)


# ─── Server control request handling ──────────────────────────────────────

_OUTBOUND_ONLY_ERROR = (
    "This session is outbound-only. "
    "Enable Remote Control locally to allow inbound control."
)


def handle_server_control_request(
    request: dict[str, Any],
    transport: Any,
    session_id: str,
    outbound_only: bool = False,
    on_interrupt: Any = None,
    on_set_model: Any = None,
    on_set_max_thinking_tokens: Any = None,
    on_set_permission_mode: Any = None,
) -> None:
    """Respond to inbound control_request messages from the server."""
    if not transport:
        return

    req = request.get("request", {})
    req_id = request.get("request_id", "")
    subtype = req.get("subtype", "")

    # Outbound-only: reject mutable requests
    if outbound_only and subtype != "initialize":
        response = {
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": req_id,
                "error": _OUTBOUND_ONLY_ERROR,
            },
        }
        transport.write({**response, "session_id": session_id})
        return

    if subtype == "initialize":
        response = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": req_id,
                "response": {
                    "commands": [],
                    "output_style": "normal",
                    "available_output_styles": ["normal"],
                    "models": [],
                    "account": {},
                    "pid": 0,
                },
            },
        }
    elif subtype == "set_model":
        if on_set_model:
            on_set_model(req.get("model"))
        response = {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": req_id},
        }
    elif subtype == "set_max_thinking_tokens":
        if on_set_max_thinking_tokens:
            on_set_max_thinking_tokens(req.get("max_thinking_tokens"))
        response = {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": req_id},
        }
    elif subtype == "set_permission_mode":
        if on_set_permission_mode:
            verdict = on_set_permission_mode(req.get("mode", ""))
        else:
            verdict = {
                "ok": False,
                "error": "set_permission_mode not supported in this context",
            }
        if verdict.get("ok"):
            response = {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": req_id},
            }
        else:
            response = {
                "type": "control_response",
                "response": {
                    "subtype": "error",
                    "request_id": req_id,
                    "error": verdict.get("error", "Unknown error"),
                },
            }
    elif subtype == "interrupt":
        if on_interrupt:
            on_interrupt()
        response = {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": req_id},
        }
    else:
        response = {
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": req_id,
                "error": f"REPL bridge does not handle control_request subtype: {subtype}",
            },
        }

    transport.write({**response, "session_id": session_id})


# ─── Result message ───────────────────────────────────────────────────────


def make_result_message(session_id: str) -> dict[str, Any]:
    """Build a minimal SDKResultSuccess for session archival."""
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 0,
        "duration_api_ms": 0,
        "is_error": False,
        "num_turns": 0,
        "result": "",
        "stop_reason": None,
        "total_cost_usd": 0,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
            "server_tool_use": None,
        },
        "modelUsage": {},
        "permission_denials": [],
        "session_id": session_id,
        "uuid": str(_uuid.uuid4()),
    }


# ─── BoundedUUIDSet ───────────────────────────────────────────────────────


class BoundedUUIDSet:
    """FIFO-bounded set backed by ring buffer. O(1) add/has, O(capacity) memory."""

    def __init__(self, capacity: int = 1000) -> None:
        self._capacity = capacity
        self._ring: list[Optional[str]] = [None] * capacity
        self._set: set[str] = set()
        self._write_idx = 0

    def add(self, item: str) -> bool:
        if item in self._set:
            return False
        evicted = self._ring[self._write_idx]
        if evicted is not None:
            self._set.discard(evicted)
        self._ring[self._write_idx] = item
        self._set.add(item)
        self._write_idx = (self._write_idx + 1) % self._capacity
        return True

    def has(self, item: str) -> bool:
        return item in self._set

    def __contains__(self, item: str) -> bool:
        return item in self._set

    def clear(self) -> None:
        self._set.clear()
        self._ring = [None] * self._capacity
        self._write_idx = 0
