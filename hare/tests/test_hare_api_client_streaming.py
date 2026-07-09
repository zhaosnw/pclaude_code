from __future__ import annotations

from types import SimpleNamespace

import pytest

from hare.services.api.client import _streaming_request_events
from hare.app_types.message import APIMessage, AssistantMessage, UserMessage
from hare.utils.messages import normalize_messages_for_api


class _FakeStream:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def __aiter__(self):
        async def _gen():
            for event in self._events:
                yield event

        return _gen()


class _FakeMessages:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    def stream(self, **_kwargs) -> _FakeStream:
        return _FakeStream(self._events)


class _FakeClient:
    def __init__(self, events: list[object]) -> None:
        self.messages = _FakeMessages(events)


@pytest.mark.asyncio
async def test_streaming_request_events_yields_per_block_not_cumulative() -> None:
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                id="msg_123", usage=SimpleNamespace(input_tokens=1)
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="我来帮你分析整个代码目录。"),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(
                type="tool_use", id="toolu_1", name="GlobTool", input={}
            ),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(output_tokens=10),
        ),
        SimpleNamespace(type="message_stop"),
    ]

    client = _FakeClient(events)
    outputs = []
    async for item in _streaming_request_events(
        client,
        {"model": "test-model", "max_tokens": 128},
    ):
        outputs.append(item)

    assistant_messages = [item for item in outputs if getattr(item, "type", None) == "assistant"]
    assert len(assistant_messages) == 2
    assert assistant_messages[0].message.id == "msg_123"
    assert assistant_messages[1].message.id == "msg_123"
    assert assistant_messages[0].message.content == [
        {"type": "text", "text": "我来帮你分析整个代码目录。"}
    ]
    assert assistant_messages[1].message.content == [
        {"type": "tool_use", "id": "toolu_1", "name": "GlobTool", "input": {}}
    ]

    merged = normalize_messages_for_api(assistant_messages, ())
    assert len(merged) == 1
    assert merged[0].message.content == [
        {"type": "text", "text": "我来帮你分析整个代码目录。"},
        {"type": "tool_use", "id": "toolu_1", "name": "GlobTool", "input": {}},
    ]


@pytest.mark.asyncio
async def test_streaming_request_events_parses_tool_use_input_json() -> None:
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                id="msg_tool", usage=SimpleNamespace(input_tokens=1)
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="tool_use", id="toolu_1", name="Bash", input={}
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"command":"pwd"}'),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(output_tokens=3),
        ),
        SimpleNamespace(type="message_stop"),
    ]

    client = _FakeClient(events)
    outputs = []
    async for item in _streaming_request_events(
        client,
        {"model": "test-model", "max_tokens": 128},
    ):
        outputs.append(item)

    assistant_messages = [
        item for item in outputs if getattr(item, "type", None) == "assistant"
    ]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].message.content == [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "Bash",
            "input": {"command": "pwd"},
        }
    ]


def test_merge_assistant_messages_preserves_message_id_across_followup_user() -> None:
    first = AssistantMessage(
        message=APIMessage(
            role="assistant",
            id="msg_merged",
            content=[{"type": "text", "text": "第一段"}],
        )
    )
    second = AssistantMessage(
        message=APIMessage(
            role="assistant",
            id="msg_merged",
            content=[{"type": "thinking", "thinking": "第二段", "signature": ""}],
        )
    )
    followup = UserMessage(
        message=APIMessage(role="user", content="继续"),
    )

    merged = normalize_messages_for_api([first, second, followup], ())
    assert len(merged) == 2
    assert merged[0].message.id == "msg_merged"
    assert merged[0].message.content == [
        {"type": "text", "text": "第一段"},
        {"type": "thinking", "thinking": "第二段", "signature": ""},
    ]
