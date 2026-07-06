"""
REPL bridge transport — v1 (HybridTransport) and v2 (SSETransport + CCRClient).

Port of: src/bridge/replBridgeTransport.ts

Transport interface with 16 methods:
  write, writeBatch, close, isConnectedStatus, getStateLabel,
  setOnData, setOnClose, setOnConnect, connect,
  getLastSequenceNum, droppedBatchCount, reportState,
  reportMetadata, reportDelivery, flush
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class ReplBridgeTransport(Protocol):
    """Transport interface for bridge communication."""

    async def write(self, event: Any) -> None: ...
    async def write_batch(self, events: list[Any]) -> None: ...
    async def close(self) -> None: ...
    def is_connected_status(self) -> bool: ...
    def get_state_label(self) -> str: ...
    def set_on_data(self, handler: Any) -> None: ...
    def set_on_close(self, handler: Any) -> None: ...
    def set_on_connect(self, handler: Any) -> None: ...
    async def connect(self) -> None: ...
    def get_last_sequence_num(self) -> Optional[int]: ...
    def dropped_batch_count(self) -> int: ...
    def report_state(self, state: str) -> None: ...
    def report_metadata(self, metadata: dict[str, Any]) -> None: ...
    def report_delivery(self, seq: int) -> None: ...
    async def flush(self) -> None: ...


async def create_v1_repl_transport(
    session_id: str,
    sdk_url: str,
    access_token: str,
    session_ingress_url: Optional[str] = None,
    on_debug: Any = None,
    create_hybrid_transport: Any = None,
) -> Optional[ReplBridgeTransport]:
    """Create v1 transport wrapping HybridTransport (WS reads + POST writes).

    Returns None if transport creation fails.
    """
    if not create_hybrid_transport:
        return None

    try:
        transport = await create_hybrid_transport(
            {
                "sdkUrl": sdk_url,
                "sessionId": session_id,
                "accessToken": access_token,
                "sessionIngressUrl": session_ingress_url,
            }
        )
        return transport
    except Exception as e:
        if on_debug:
            on_debug(f"[bridge:transport] v1 transport creation failed: {e}")
        return None


async def create_v2_repl_transport(
    session_id: str,
    api_base_url: str,
    worker_jwt: str,
    worker_epoch: int,
    outbound_only: bool = False,
    on_debug: Any = None,
    create_sse_transport: Any = None,
    create_ccr_client: Any = None,
) -> Optional[ReplBridgeTransport]:
    """Create v2 transport wrapping SSETransport + CCRClient.

    Sets up SSE inbound stream and CCR client for heartbeat/state/event requests.
    Returns None if transport creation fails.
    """
    if not create_sse_transport or not create_ccr_client:
        return None

    try:
        # Create CCR client for outbound requests
        ccr_client = await create_ccr_client(
            {
                "sessionId": session_id,
                "apiBaseUrl": api_base_url,
                "workerJwt": worker_jwt,
                "workerEpoch": worker_epoch,
            }
        )

        # Create SSE transport for inbound stream
        sse_transport = await create_sse_transport(
            {
                "sessionId": session_id,
                "apiBaseUrl": api_base_url,
                "workerJwt": worker_jwt,
                "outboundOnly": outbound_only,
            }
        )

        # Wire them together into a unified transport
        return _UnifiedV2Transport(sse_transport, ccr_client, on_debug)
    except Exception as e:
        if on_debug:
            on_debug(f"[bridge:transport] v2 transport creation failed: {e}")
        return None


class _UnifiedV2Transport:
    """Unified v2 transport combining SSE inbound + CCR outbound."""

    def __init__(self, sse: Any, ccr: Any, on_debug: Any = None) -> None:
        self._sse = sse
        self._ccr = ccr
        self._on_debug = on_debug
        self._on_data: Any = None
        self._on_close: Any = None
        self._on_connect: Any = None
        self._connected = False
        self._seq = 0
        self._dropped = 0

    async def write(self, event: Any) -> None:
        if self._ccr:
            await self._ccr.send_event(event)

    async def write_batch(self, events: list[Any]) -> None:
        for event in events:
            await self.write(event)

    async def close(self) -> None:
        self._connected = False
        if self._sse:
            await self._sse.close()
        if self._ccr:
            await self._ccr.close()

    def is_connected_status(self) -> bool:
        return self._connected

    def get_state_label(self) -> str:
        return "connected" if self._connected else "disconnected"

    def set_on_data(self, handler: Any) -> None:
        self._on_data = handler
        if self._sse and hasattr(self._sse, "set_on_data"):
            self._sse.set_on_data(handler)

    def set_on_close(self, handler: Any) -> None:
        self._on_close = handler
        if self._sse and hasattr(self._sse, "set_on_close"):
            self._sse.set_on_close(handler)

    def set_on_connect(self, handler: Any) -> None:
        self._on_connect = handler

    async def connect(self) -> None:
        self._connected = True
        if self._sse and hasattr(self._sse, "connect"):
            await self._sse.connect()

    def get_last_sequence_num(self) -> Optional[int]:
        return self._seq

    def dropped_batch_count(self) -> int:
        return self._dropped

    def report_state(self, state: str) -> None:
        if self._ccr and hasattr(self._ccr, "report_state"):
            self._ccr.report_state(state)

    def report_metadata(self, metadata: dict[str, Any]) -> None:
        if self._ccr and hasattr(self._ccr, "report_metadata"):
            self._ccr.report_metadata(metadata)

    def report_delivery(self, seq: int) -> None:
        self._seq = seq

    async def flush(self) -> None:
        pass
