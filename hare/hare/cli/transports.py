"""
CLI transports — stdio, NDJSON, SSE and WebSocket.

Port of: src/cli/transports/
"""

from __future__ import annotations

import json
import sys
from typing import Any, AsyncIterator, Protocol


class Transport(Protocol):
    async def read(self) -> str | None: ...

    async def write(self, data: str) -> None: ...

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# StdioTransport — line-based stdin/stdout
# ---------------------------------------------------------------------------


class StdioTransport:
    def __init__(self, input_stream: Any = None, output_stream: Any = None):
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout

    async def read(self) -> str | None:
        try:
            line = self._input.readline()
            return line.rstrip("\n") if line else None
        except EOFError:
            return None

    async def write(self, data: str) -> None:
        self._output.write(data + "\n")
        self._output.flush()

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# NdjsonTransport — newline-delimited JSON over streams
# ---------------------------------------------------------------------------


class NdjsonTransport:
    def __init__(self, input_stream: Any = None, output_stream: Any = None):
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout

    async def read(self) -> dict[str, Any] | None:
        try:
            line = self._input.readline()
            if not line:
                return None
            return json.loads(line)
        except (json.JSONDecodeError, EOFError):
            return None

    async def write(self, data: Any) -> None:
        self._output.write(json.dumps(data, separators=(",", ":")) + "\n")
        self._output.flush()

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# SSETransport — Server-Sent Events stream
# ---------------------------------------------------------------------------


class SSETransport:
    def __init__(
        self,
        url: str = "",
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ):
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self._connected = False

    async def connect(self) -> None:
        """Establish SSE connection."""
        import httpx

        self._client = httpx.AsyncClient(timeout=self.timeout)
        self._response = await self._client.send(
            self._client.build_request(
                "GET",
                self.url,
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                    **self.headers,
                },
            ),
            stream=True,
        )
        self._response.raise_for_status()
        self._connected = True

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield SSE events as parsed dicts."""
        if not self._connected:
            await self.connect()

        event_type = ""
        data_buffer = ""

        async for line in self._response.aiter_lines():
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_buffer += line[5:].strip()
            elif line == "" and data_buffer:
                # Empty line = end of event
                try:
                    yield {
                        "event": event_type or "message",
                        "data": json.loads(data_buffer),
                    }
                except json.JSONDecodeError:
                    yield {
                        "event": event_type or "message",
                        "data": data_buffer,
                    }
                event_type = ""
                data_buffer = ""

    async def close(self) -> None:
        if hasattr(self, "_client"):
            await self._client.aclose()
            self._connected = False


# ---------------------------------------------------------------------------
# WebSocketTransport — bidirectional JSON messaging
# ---------------------------------------------------------------------------


class WebSocketTransport:
    def __init__(
        self,
        url: str = "",
        headers: dict[str, str] | None = None,
    ):
        self.url = url
        self.headers = headers or {}
        self._ws = None

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        import websockets

        extra_headers = [(k, v) for k, v in self.headers.items()]
        self._ws = await websockets.connect(self.url, extra_headers=extra_headers)

    async def read(self) -> dict[str, Any] | None:
        if self._ws is None:
            await self.connect()
        try:
            data = await self._ws.recv()
            if isinstance(data, str):
                return json.loads(data)
            return json.loads(data.decode("utf-8"))
        except Exception:
            return None

    async def write(self, data: Any) -> None:
        if self._ws is None:
            await self.connect()
        await self._ws.send(json.dumps(data, default=str))

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
