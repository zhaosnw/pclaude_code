from __future__ import annotations

import pytest

from hare.bootstrap.state import set_session_persistence_disabled
from hare.sdk import HareClient, HareClientOptions


@pytest.fixture(autouse=True)
def _disable_session_persistence() -> None:
    set_session_persistence_disabled(True)
    yield
    set_session_persistence_disabled(False)


@pytest.mark.asyncio
async def test_sdk_client_ask_returns_result_event() -> None:
    client = await HareClient.create(HareClientOptions())
    result = await client.ask("Reply with a short test acknowledgement.")
    assert result["type"] == "result"
    assert "session_id" in result


@pytest.mark.asyncio
async def test_sdk_client_stream_yields_result_event() -> None:
    client = await HareClient.create(HareClientOptions())
    events = []
    async for event in client.stream("Reply with a short streaming acknowledgement."):
        events.append(event)
    assert events
    assert any(event.get("type") == "result" for event in events)
