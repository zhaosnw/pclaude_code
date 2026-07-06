"""
/bridge-kick command - inject bridge failure states for manual recovery testing.

Port of: src/commands/bridge-kick.ts

Ant-only debugging tool. Subcommands:
  close <code>              Fire ws_closed with code (e.g. 1002)
  poll <status> [type]      Next poll throws BridgeFatalError(status, type)
  poll transient            Next poll throws axios-style rejection
  register fail [N]         Next N registers transient-fail
  register fatal            Next register 403s
  reconnect-session fail    Next POST /bridge/reconnect fails
  heartbeat <status>        Next heartbeat throws BridgeFatalError(status)
  reconnect                 Call reconnectEnvironmentWithSession directly
  status                    Print bridge state
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "bridge-kick"
DESCRIPTION = "Inject bridge failure states for manual recovery testing"
ALIASES: list[str] = []

USAGE = """/bridge-kick <subcommand>
  close <code>              fire ws_closed with the given code (e.g. 1002)
  poll <status> [type]      next poll throws BridgeFatalError(status, type)
  poll transient            next poll throws axios-style rejection (5xx/net)
  register fail [N]         next N registers transient-fail (default 1)
  register fatal            next register 403s (terminal)
  reconnect-session fail    next POST /bridge/reconnect fails
  heartbeat <status>        next heartbeat throws BridgeFatalError(status)
  reconnect                 call reconnectEnvironmentWithSession directly
  status                    print bridge state"""


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Execute bridge fault injection subcommands."""
    get_bridge_debug_handle = context.get("get_bridge_debug_handle")

    if not get_bridge_debug_handle:
        return {
            "type": "text",
            "value": "No bridge debug handle registered. Remote Control must be connected (USER_TYPE=ant).",
        }

    handle = get_bridge_debug_handle()
    if not handle:
        return {
            "type": "text",
            "value": "No bridge debug handle registered. Remote Control must be connected (USER_TYPE=ant).",
        }

    parts = args.strip().split()
    sub = parts[0] if parts else ""
    a = parts[1] if len(parts) > 1 else ""
    b = parts[2] if len(parts) > 2 else ""

    if sub == "close":
        try:
            code = int(a)
        except (ValueError, TypeError):
            return {"type": "text", "value": f"close: need a numeric code\n{USAGE}"}
        handle.fire_close(code)
        return {
            "type": "text",
            "value": f"Fired transport close({code}). Watch debug.log for [bridge:repl] recovery.",
        }

    elif sub == "poll":
        if a == "transient":
            handle.inject_fault(
                {
                    "method": "pollForWork",
                    "kind": "transient",
                    "status": 503,
                    "count": 1,
                }
            )
            handle.wake_poll_loop()
            return {
                "type": "text",
                "value": "Next poll will throw a transient (axios rejection). Poll loop woken.",
            }
        try:
            status = int(a)
        except (ValueError, TypeError):
            return {
                "type": "text",
                "value": f"poll: need 'transient' or a status code\n{USAGE}",
            }
        error_type = (
            b if b else ("not_found_error" if status == 404 else "authentication_error")
        )
        handle.inject_fault(
            {
                "method": "pollForWork",
                "kind": "fatal",
                "status": status,
                "errorType": error_type,
                "count": 1,
            }
        )
        handle.wake_poll_loop()
        return {
            "type": "text",
            "value": f"Next poll will throw BridgeFatalError({status}, {error_type}). Poll loop woken.",
        }

    elif sub == "register":
        if a == "fatal":
            handle.inject_fault(
                {
                    "method": "registerBridgeEnvironment",
                    "kind": "fatal",
                    "status": 403,
                    "errorType": "permission_error",
                    "count": 1,
                }
            )
            return {
                "type": "text",
                "value": "Next registerBridgeEnvironment will 403. Trigger with close/reconnect.",
            }
        try:
            n = int(b) if b else 1
        except (ValueError, TypeError):
            n = 1
        handle.inject_fault(
            {
                "method": "registerBridgeEnvironment",
                "kind": "transient",
                "status": 503,
                "count": n,
            }
        )
        return {
            "type": "text",
            "value": f"Next {n} registerBridgeEnvironment call(s) will transient-fail. Trigger with close/reconnect.",
        }

    elif sub == "reconnect-session":
        handle.inject_fault(
            {
                "method": "reconnectSession",
                "kind": "fatal",
                "status": 404,
                "errorType": "not_found_error",
                "count": 2,
            }
        )
        return {
            "type": "text",
            "value": "Next 2 POST /bridge/reconnect calls will 404. doReconnect Strategy 1 falls through to Strategy 2.",
        }

    elif sub == "heartbeat":
        try:
            status = int(a) if a else 401
        except (ValueError, TypeError):
            status = 401
        handle.inject_fault(
            {
                "method": "heartbeatWork",
                "kind": "fatal",
                "status": status,
                "errorType": "authentication_error"
                if status == 401
                else "not_found_error",
                "count": 1,
            }
        )
        return {
            "type": "text",
            "value": f"Next heartbeat will {status}. Watch for onHeartbeatFatal -> work-state teardown.",
        }

    elif sub == "reconnect":
        handle.force_reconnect()
        return {
            "type": "text",
            "value": "Called reconnectEnvironmentWithSession(). Watch debug.log.",
        }

    elif sub == "status":
        return {
            "type": "text",
            "value": handle.describe()
            if hasattr(handle, "describe")
            else "Bridge handle active.",
        }

    else:
        return {"type": "text", "value": USAGE}


def is_enabled() -> bool:
    """Only enabled for ant users (mirrors TS isEnabled check)."""
    import os

    return os.environ.get("USER_TYPE") == "ant"


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "isEnabled": is_enabled,
        "supportsNonInteractive": False,
        "call": call,
    }
