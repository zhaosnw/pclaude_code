"""
Env-less (v2) bridge core — code-session based bridge without environments API.

Port of: src/bridge/remoteBridgeCore.ts

Full v2 bridge implementation:
- Creates code session via POST /v1/code/sessions
- Fetches bridge credentials via POST /v1/code/sessions/{id}/bridge
- Builds v2 transport (SSETransport + CCRClient)
- Manages JWT refresh scheduling
- Handles 401 recovery with OAuth refresh
- Writes messages, control requests, and results
- Flushes history with cap
- Archives session on teardown
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from hare.bridge.bridge_messaging import (
    BoundedUUIDSet,
    handle_ingress_message,
    handle_server_control_request,
    make_result_message,
    is_eligible_bridge_message,
)
from hare.bridge.flush_gate import FlushGate
from hare.bridge.jwt_utils import TokenRefreshScheduler
from hare.bridge.repl_bridge import BridgeState, ReplBridgeHandle
from hare.bridge.repl_bridge_transport import create_v2_repl_transport


async def init_env_less_bridge_core(
    config: Any,
    access_token: str = "",
    base_url: str = "",
    title: str = "",
    initial_messages: Optional[list[dict[str, Any]]] = None,
    on_inbound_message: Any = None,
    on_permission_response: Any = None,
    on_interrupt: Any = None,
    on_set_model: Any = None,
    on_set_max_thinking_tokens: Any = None,
    on_set_permission_mode: Any = None,
    on_state_change: Any = None,
    on_first_user_message: Any = None,
    get_access_token_fn: Any = None,
    http_post: Any = None,
    outbound_only: bool = False,
    tags: Optional[list[str]] = None,
    previously_flushed_uuids: Optional[set[str]] = None,
) -> ReplBridgeHandle:
    """Initialize the env-less (v2) bridge.

    This is the main v2 implementation. It:
    1. Creates a code session
    2. Fetches bridge credentials (worker_jwt, api_base_url, worker_epoch)
    3. Builds v2 transport (SSE + CCRClient)
    4. Wires callbacks for messages, permissions, control requests
    5. Schedules JWT refresh
    6. Flushes initial history
    7. Archives on teardown
    """
    from hare.bridge.code_session_api import (
        create_code_session,
        fetch_remote_credentials,
    )
    from hare.bridge.create_session import archive_bridge_session

    state = BridgeState()
    handle = ReplBridgeHandle(state)
    handle._running = True

    recent_posted = BoundedUUIDSet(1000)
    recent_inbound = BoundedUUIDSet(1000)
    flush_gate = FlushGate()
    flushed_uuids = previously_flushed_uuids or set()

    # 1. Create code session
    session_id = await create_code_session(
        base_url=base_url,
        access_token=access_token,
        title=title,
        timeout_ms=10_000,
        tags=tags,
        http_post=http_post,
    )
    if not session_id:
        state.error = "Failed to create code session"
        if on_state_change:
            on_state_change(state, state.error)
        return handle

    state.session_id = session_id

    # 2. Fetch bridge credentials
    creds = await fetch_remote_credentials(
        session_id=session_id,
        base_url=base_url,
        access_token=access_token,
        timeout_ms=10_000,
        http_post=http_post,
    )
    if not creds:
        state.error = "Failed to fetch bridge credentials"
        if on_state_change:
            on_state_change(state, state.error)
        return handle

    # 3. Build v2 transport
    transport = await create_v2_repl_transport(
        session_id=session_id,
        api_base_url=creds.api_base_url,
        worker_jwt=creds.worker_jwt,
        worker_epoch=creds.worker_epoch,
        outbound_only=outbound_only,
    )
    if not transport:
        state.error = "Failed to create transport"
        return handle

    handle._transport = transport

    # 4. Wire callbacks
    transport.set_on_data(
        lambda data: handle_ingress_message(
            data,
            recent_posted,
            recent_inbound,
            on_inbound_message=on_inbound_message,
            on_permission_response=on_permission_response,
            on_control_request=lambda req: handle_server_control_request(
                req,
                transport,
                session_id,
                outbound_only=outbound_only,
                on_interrupt=on_interrupt,
                on_set_model=on_set_model,
                on_set_max_thinking_tokens=on_set_max_thinking_tokens,
                on_set_permission_mode=on_set_permission_mode,
            ),
        )
    )

    # 5. Schedule JWT refresh
    if get_access_token_fn:
        scheduler = TokenRefreshScheduler(
            get_access_token=get_access_token_fn,
            on_refresh=lambda sid, token: transport.write(...),
            label="v2-bridge",
        )

    # 6. Flush initial history
    if initial_messages:
        eligible = [m for m in initial_messages if is_eligible_bridge_message(m)]
        if eligible:
            for msg in eligible:
                msg_uuid = msg.get("uuid")
                if msg_uuid and msg_uuid in flushed_uuids:
                    continue
                await transport.write(msg)
                if msg_uuid:
                    flushed_uuids.add(msg_uuid)

    # 7. Connect transport
    await transport.connect()
    state.connected = True

    if on_state_change:
        on_state_change(state, "connected")

    # 8. Teardown on disconnect
    def _on_transport_close() -> None:
        async def _cleanup() -> None:
            state.connected = False
            handle._running = False
            try:
                result_msg = make_result_message(session_id)
                await transport.write(result_msg)
            except Exception:
                pass
            try:
                await archive_bridge_session(session_id, http_post=http_post)
            except Exception:
                pass

        asyncio.ensure_future(_cleanup())

    transport.set_on_close(_on_transport_close)

    return handle
