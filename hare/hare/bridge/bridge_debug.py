"""
Fault injection system for manually testing bridge recovery paths.

Port of: src/bridge/bridgeDebug.ts

Module-level state: one bridge per REPL process.
BridgeDebugHandle is used by /bridge-kick slash command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class BridgeFault:
    method: str = ""
    kind: str = "fatal"  # 'fatal' | 'transient'
    status: int = 500
    error_type: Optional[str] = None
    count: int = 1


class BridgeDebugHandle:
    def __init__(self) -> None:
        self._on_close: Any = None
        self._on_reconnect: Any = None
        self._on_wake_poll: Any = None
        self._env_id: str = ""
        self._session_ids: list[str] = []

    def set_handlers(
        self,
        fire_close: Any,
        force_reconnect: Any,
        wake_poll_loop: Any,
        env_id: str = "",
        session_ids: Optional[list[str]] = None,
    ) -> None:
        self._on_close = fire_close
        self._on_reconnect = force_reconnect
        self._on_wake_poll = wake_poll_loop
        self._env_id = env_id
        self._session_ids = session_ids or []

    def fire_close(self, code: int) -> None:
        if self._on_close:
            self._on_close(code)

    def force_reconnect(self) -> None:
        if self._on_reconnect:
            self._on_reconnect()

    def inject_fault(self, fault: dict[str, Any]) -> None:
        f = BridgeFault(
            method=fault.get("method", ""),
            kind=fault.get("kind", "fatal"),
            status=fault.get("status", 500),
            error_type=fault.get("errorType"),
            count=fault.get("count", 1),
        )
        inject_bridge_fault(f)

    def wake_poll_loop(self) -> None:
        if self._on_wake_poll:
            self._on_wake_poll()

    def describe(self) -> str:
        return f"BridgeDebugHandle(env={self._env_id}, sessions={self._session_ids})"


# Module-level state
_debug_handle: Optional[BridgeDebugHandle] = None
_fault_queue: list[BridgeFault] = []


def register_bridge_debug_handle(handle: BridgeDebugHandle) -> None:
    global _debug_handle
    _debug_handle = handle


def clear_bridge_debug_handle() -> None:
    global _debug_handle, _fault_queue
    _debug_handle = None
    _fault_queue.clear()


def get_bridge_debug_handle() -> Optional[BridgeDebugHandle]:
    return _debug_handle


def inject_bridge_fault(fault: BridgeFault) -> None:
    _fault_queue.append(fault)


def consume_fault(method: str) -> Optional[BridgeFault]:
    for i, f in enumerate(_fault_queue):
        if f.method == method:
            f.count -= 1
            if f.count <= 0:
                _fault_queue.pop(i)
            return f
    return None


class BridgeFatalError(Exception):
    def __init__(
        self, message: str, status: int, error_type: Optional[str] = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


def wrap_api_for_fault_injection(api: Any) -> Any:
    """Wrap a BridgeApiClient so each call checks the fault queue first."""

    class _FaultInjectedApi:
        async def register_bridge_environment(self, config: Any) -> Any:
            f = consume_fault("registerBridgeEnvironment")
            if f:
                _throw_fault(f, "Registration")
            return await api.register_bridge_environment(config)

        async def poll_for_work(
            self,
            env_id: str,
            secret: str,
            signal: Any = None,
            reclaim_ms: Optional[int] = None,
        ) -> Any:
            f = consume_fault("pollForWork")
            if f:
                _throw_fault(f, "Poll")
            return await api.poll_for_work(env_id, secret, signal, reclaim_ms)

        async def acknowledge_work(self, *args: Any, **kwargs: Any) -> Any:
            return await api.acknowledge_work(*args, **kwargs)

        async def stop_work(self, *args: Any, **kwargs: Any) -> Any:
            return await api.stop_work(*args, **kwargs)

        async def deregister_environment(self, *args: Any, **kwargs: Any) -> Any:
            return await api.deregister_environment(*args, **kwargs)

        async def send_permission_response_event(
            self, *args: Any, **kwargs: Any
        ) -> Any:
            return await api.send_permission_response_event(*args, **kwargs)

        async def archive_session(self, *args: Any, **kwargs: Any) -> Any:
            return await api.archive_session(*args, **kwargs)

        async def reconnect_session(self, env_id: str, session_id: str) -> Any:
            f = consume_fault("reconnectSession")
            if f:
                _throw_fault(f, "ReconnectSession")
            return await api.reconnect_session(env_id, session_id)

        async def heartbeat_work(self, env_id: str, work_id: str, token: str) -> Any:
            f = consume_fault("heartbeatWork")
            if f:
                _throw_fault(f, "Heartbeat")
            return await api.heartbeat_work(env_id, work_id, token)

    return _FaultInjectedApi()


def _throw_fault(fault: BridgeFault, context: str) -> None:
    if fault.kind == "fatal":
        raise BridgeFatalError(
            f"[injected] {context} {fault.status}",
            fault.status,
            fault.error_type,
        )
    raise OSError(f"[injected transient] {context} {fault.status}")
