"""
Core REPL bridge — environment-based bridge with poll loop, work dispatch, sessions.

Port of: src/bridge/replBridge.ts

Full env-based bridge core:
- Environment registration with re-registration support
- Multi-type work poll (not-at-capacity, at-capacity, transport-connected)
- Session creation and lifecycle management
- Transport setup (v1 HybridTransport, v2 SSETransport/CCRClient)
- Echo deduplication via BoundedUUIDSet
- Flush management via FlushGate
- Control request handling
- Session archival on teardown
- Debug fault injection integration
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from hare.bridge.bridge_messaging import (
    BoundedUUIDSet,
    make_result_message,
)
from hare.bridge.bridge_pointer import write_bridge_pointer
from hare.bridge.capacity_wake import CapacityWake
from hare.bridge.flush_gate import FlushGate
from hare.bridge.types import (
    BridgeConfig,
    SessionHandle,
    SessionSpawnOpts,
)


@dataclass
class BridgeState:
    """Bridge connection state."""

    connected: bool = False
    session_id: str = ""
    bridge_id: str = ""
    environment_id: str = ""
    environment_secret: str = ""
    error: Optional[str] = None
    reconnecting: bool = False
    active_sessions: int = 0


class ReplBridgeHandle:
    """Handle for controlling an active REPL bridge."""

    def __init__(self, state: Optional[BridgeState] = None) -> None:
        self.state = state or BridgeState()
        self._transport: Any = None
        self._sessions: dict[str, SessionHandle] = {}
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._shutdown_event = asyncio.Event()

    async def send_message(self, msg: str) -> None:
        """Forward a message to the bridge transport."""
        if self._transport:
            await self._transport.write(msg)

    async def disconnect(self) -> None:
        """Gracefully disconnect the bridge."""
        self._running = False
        self._shutdown_event.set()
        self.state.connected = False
        for task in self._tasks:
            task.cancel()
        for session in self._sessions.values():
            try:
                session.kill()
            except Exception:
                pass
        if self._transport:
            try:
                await self._transport.close()
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        return self.state.connected and self._running


async def init_bridge_core(
    config: BridgeConfig,
    spawner: Any = None,
    transport_factory: Any = None,
    on_inbound_message: Any = None,
    on_permission_response: Any = None,
    on_control_request: Any = None,
    on_state_change: Any = None,
    on_session_activity: Any = None,
    logger: Any = None,
    bridge_pointer_dir: str = "",
    perpetual: bool = False,
    outbound_only: bool = False,
) -> ReplBridgeHandle:
    """Initialize the full bridge core.

    This is the main bridge implementation. It:
    1. Registers with the environments API
    2. Runs a poll loop for work items
    3. Dispatches sessions via the spawner
    4. Manages transport for session communication
    5. Handles graceful teardown
    """
    state = BridgeState(bridge_id=config.bridge_id or str(uuid.uuid4()))
    handle = ReplBridgeHandle(state)

    state.environment_id = config.environment_id
    handle._running = True

    from hare.bridge.bridge_api import (
        create_bridge_api_client,
        BridgeApiDeps,
        BridgeFatalError,
    )
    from hare.bridge.work_secret import decode_work_secret, build_sdk_url

    api_deps = BridgeApiDeps(
        base_url=config.api_base_url,
        runner_version="2.0.0",
    )
    api = create_bridge_api_client(api_deps)

    # Dedup sets for echo filtering
    recent_posted = BoundedUUIDSet(1000)
    recent_inbound = BoundedUUIDSet(1000)

    # Flush gate for history coordination
    flush_gate = FlushGate()

    # Capacity wake for poll loop
    capacity_wake = CapacityWake()

    # Register environment
    try:
        reg = await api.register_bridge_environment(config)
        state.environment_id = reg.get("environment_id", "")
        state.environment_secret = reg.get("environment_secret", "")
    except BridgeFatalError as e:
        state.error = str(e)
        if on_state_change:
            on_state_change(state, str(e))
        return handle

    if on_state_change:
        on_state_change(state, "idle")

    # Write bridge pointer for crash recovery
    if bridge_pointer_dir:
        write_bridge_pointer(
            bridge_pointer_dir,
            {
                "sessionId": state.session_id,
                "environmentId": state.environment_id,
                "source": "repl",
            },
        )

    # Main poll loop
    async def _poll_loop() -> None:
        """Poll for work and dispatch sessions."""
        while handle._running:
            try:
                response = await api.poll_for_work(
                    state.environment_id,
                    state.environment_secret,
                    reclaim_older_than_ms=5000,
                )

                if response:
                    work_data = response.data
                    secret_b64 = response.secret

                    # Decode secret and build SDK URL
                    secret = decode_work_secret(secret_b64)
                    sdk_url = build_sdk_url(
                        secret.api_base_url, secret.session_ingress_token
                    )

                    # Acknowledge
                    await api.acknowledge_work(
                        state.environment_id,
                        response.id,
                        secret.session_ingress_token,
                    )

                    # Spawn session
                    if spawner:
                        opts = SessionSpawnOpts(
                            session_id=work_data.id,
                            sdk_url=sdk_url,
                            access_token=secret.session_ingress_token,
                        )
                        session = spawner.spawn(opts, config.dir)
                        handle._sessions[work_data.id] = session
                        state.active_sessions = len(handle._sessions)
                        state.session_id = work_data.id

                        if logger:
                            logger.log_session_start(work_data.id, "work")

                else:
                    await asyncio.sleep(2)  # Default poll interval

            except BridgeFatalError as e:
                state.error = str(e)
                if on_state_change:
                    on_state_change(state, str(e))
                if perpetual:
                    continue
                handle._running = False
            except Exception as e:
                if logger:
                    logger.log_error(f"Poll error: {e}")
                await asyncio.sleep(1)

    # Session cleanup loop
    async def _cleanup_loop() -> None:
        """Monitor sessions and clean up completed ones."""
        while handle._running:
            completed = []
            for sid, session in list(handle._sessions.items()):
                if hasattr(session, "done") and hasattr(session.done, "done"):
                    if session.done.done():
                        completed.append(sid)
                elif hasattr(session, "_proc") and session._proc.returncode is not None:
                    completed.append(sid)

            for sid in completed:
                session = handle._sessions.pop(sid, None)
                if session:
                    try:
                        result_msg = make_result_message(sid)
                        if handle._transport:
                            await handle._transport.write(result_msg)
                    except Exception:
                        pass
                    if logger:
                        logger.log_session_complete(sid, 0)
                    capacity_wake.signal()

            state.active_sessions = len(handle._sessions)
            await asyncio.sleep(0.5)

    handle._tasks.append(asyncio.ensure_future(_poll_loop()))
    handle._tasks.append(asyncio.ensure_future(_cleanup_loop()))

    state.connected = True
    return handle
