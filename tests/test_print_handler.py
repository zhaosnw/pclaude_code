from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest

from hare.cli.print_handler import stream_results
from hare.query_engine import QueryEngine, QueryEngineConfig
from hare.app_types.message import APIMessage, AssistantMessage, StreamEvent


async def _iter_messages(*messages: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
    for message in messages:
        yield message


@pytest.mark.asyncio
async def test_stream_results_prints_text_deltas_from_stream_events(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stream_results(
        _iter_messages(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "目录概览"},
                },
            },
            {"type": "result", "result": "完成"},
        ),
        output_format="text",
    )

    captured = capsys.readouterr()
    assert "目录概览" in captured.out


@pytest.mark.asyncio
async def test_query_engine_result_joins_all_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_query(_params: Any) -> AsyncGenerator[AssistantMessage, None]:
        yield AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "第一段"},
                    {"type": "tool_use", "id": "toolu_1", "name": "GlobTool"},
                    {"type": "text", "text": "第二段"},
                ],
            )
        )

    monkeypatch.setattr("hare.query_engine.query", fake_query)
    async def fake_get_slash_command_tool_skills(_cwd: str) -> list[Any]:
        return []

    monkeypatch.setattr(
        "hare.query_engine.get_slash_command_tool_skills",
        fake_get_slash_command_tool_skills,
    )
    monkeypatch.setattr("hare.utils.model.get_main_loop_model", lambda: "test-model")

    engine = QueryEngine(QueryEngineConfig(cwd="/tmp", tools=[], commands=[]))

    events = []
    async for event in engine.submit_message("分析一下整个目录"):
        events.append(event)

    result = next(event for event in events if event["type"] == "result")
    assert result["result"] == "第一段\n第二段"


@pytest.mark.asyncio
async def test_query_engine_result_uses_last_assistant_with_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_query(_params: Any) -> AsyncGenerator[AssistantMessage, None]:
        yield AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[{"type": "text", "text": "最终总结"}],
            )
        )
        yield AssistantMessage(
            message=APIMessage(
                role="assistant",
                content=[{"type": "tool_use", "id": "toolu_1", "name": "Read"}],
            )
        )

    async def fake_get_slash_command_tool_skills(_cwd: str) -> list[Any]:
        return []

    monkeypatch.setattr("hare.query_engine.query", fake_query)
    monkeypatch.setattr(
        "hare.query_engine.get_slash_command_tool_skills",
        fake_get_slash_command_tool_skills,
    )
    monkeypatch.setattr("hare.utils.model.get_main_loop_model", lambda: "test-model")

    engine = QueryEngine(QueryEngineConfig(cwd="/tmp", tools=[], commands=[]))

    events = []
    async for event in engine.submit_message("请分析"):
        events.append(event)

    result = next(event for event in events if event["type"] == "result")
    assert result["result"] == "最终总结"


@pytest.mark.asyncio
async def test_query_engine_yields_stream_events_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_query(_params: Any) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "思考中"},
            }
        )

    async def fake_get_slash_command_tool_skills(_cwd: str) -> list[Any]:
        return []

    monkeypatch.setattr("hare.query_engine.query", fake_query)
    monkeypatch.setattr(
        "hare.query_engine.get_slash_command_tool_skills",
        fake_get_slash_command_tool_skills,
    )
    monkeypatch.setattr("hare.utils.model.get_main_loop_model", lambda: "test-model")

    engine = QueryEngine(QueryEngineConfig(cwd="/tmp", tools=[], commands=[]))

    events = []
    async for event in engine.submit_message("请展示 thinking"):
        events.append(event)

    stream_events = [event for event in events if event["type"] == "stream_event"]
    assert len(stream_events) == 1
    assert stream_events[0]["event"]["delta"]["text"] == "思考中"
