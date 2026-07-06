"""
Remote structured IO — bidirectional streaming with transport + CCR integration.

Port of: src/cli/remoteIO.ts

Extends StructuredIO with WebSocket transport, CCR v2 support,
keepalive, and session state listeners.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional

from hare.cli.structured_io import StructuredIO
from hare.cli.transport_utils import get_transport_for_url


class RemoteIO(StructuredIO):
    """Bidirectional streaming for SDK mode with session tracking."""

    def __init__(
        self,
        stream_url: str,
        initial_prompt: Any = None,
        replay_user_messages: bool = False,
        get_session_id: Optional[Callable[[], str]] = None,
        get_session_ingress_auth_token: Optional[Callable[[], Optional[str]]] = None,
        get_poll_interval_config: Optional[Callable[[], Any]] = None,
    ) -> None:
        # Create input stream
        import io

        input_stream = io.StringIO()
        super().__init__(input_stream, replay_user_messages)
        self._input_stream = input_stream

        # Prepare auth headers
        headers: dict[str, str] = {}
        if get_session_ingress_auth_token:
            token = get_session_ingress_auth_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        er_version = os.environ.get("CLAUDE_CODE_ENVIRONMENT_RUNNER_VERSION")
        if er_version:
            headers["x-environment-runner-version"] = er_version

        # Dynamic header refresh
        def refresh_headers() -> dict[str, str]:
            h: dict[str, str] = {}
            if get_session_ingress_auth_token:
                fresh = get_session_ingress_auth_token()
                if fresh:
                    h["Authorization"] = f"Bearer {fresh}"
            v = os.environ.get("CLAUDE_CODE_ENVIRONMENT_RUNNER_VERSION")
            if v:
                h["x-environment-runner-version"] = v
            return h

        # Get transport
        self._url = stream_url
        self._transport = get_transport_for_url(
            stream_url,
            headers=headers,
            session_id=get_session_id() if get_session_id else None,
            refresh_headers=refresh_headers,
        )

        self._is_bridge = os.environ.get("CLAUDE_CODE_ENVIRONMENT_KIND") == "bridge"
        self._ccr_client: Any = None
        self._keepalive_timer: Optional[asyncio.Task[Any]] = None

        # Wire transport data callback → feeds into StructuredIO's input
        self._transport.set_on_data(self._on_transport_data)

    def _on_transport_data(self, data: str) -> None:
        """Feed transport data into the StructuredIO input stream."""
        if hasattr(self._input_stream, "write"):
            self._input_stream.write(data)

    async def connect(self) -> None:
        """Connect transport and start keepalive if bridge."""
        await self._transport.connect()
        if self._is_bridge:
            self._keepalive_timer = asyncio.ensure_future(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Periodic keepalive for bridge sessions."""
        while True:
            await asyncio.sleep(30)
            try:
                # Send keepalive via transport
                pass
            except Exception:
                break

    async def write(self, message: Any) -> None:
        """Write through transport or CCR client."""
        if self._ccr_client:
            await self._ccr_client.write_event(message)
        else:
            await self._transport.write(message)

    async def flush_internal_events(self) -> None:
        if self._ccr_client:
            await self._ccr_client.flush_internal_events()

    @property
    def internal_events_pending(self) -> int:
        if self._ccr_client:
            return self._ccr_client.internal_events_pending
        return 0

    def close(self) -> None:
        if self._keepalive_timer:
            self._keepalive_timer.cancel()
        if self._ccr_client:
            self._ccr_client.close()
        self._transport.close()

    @property
    def transport(self) -> Any:
        return self._transport
