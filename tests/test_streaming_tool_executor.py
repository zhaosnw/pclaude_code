from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from hare.app_types.message import APIMessage, AssistantMessage
from hare.query.query_test_helpers import allow_all_can_use_tool, make_tool_use_context
from hare.services.tools.streaming_tool_executor import StreamingToolExecutor
from hare.tool import ToolBase, ToolResult


def _assistant_with_tools(*blocks: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        message=APIMessage(
            role="assistant",
            content=list(blocks),
            stop_reason="tool_use",
        ),
    )


@dataclass
class _Recorder:
    started: list[str]
    finished: list[str]


class _ConcurrentReadTool(ToolBase):
    def __init__(self, recorder: _Recorder, name: str) -> None:
        self.name = name
        self.aliases = []
        self.search_hint = name
        self._recorder = recorder

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return True

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def call(
        self,
        args: dict[str, Any],
        context: Any,
        can_use_tool: Any,
        parent_message: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        self._recorder.started.append(self.name)
        await asyncio.sleep(0.01)
        self._recorder.finished.append(self.name)
        return ToolResult(data={"tool": self.name})


class _ExclusiveWriteTool(_ConcurrentReadTool):
    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return False

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False


class _ContextModifyingReadTool(_ConcurrentReadTool):
    async def call(
        self,
        args: dict[str, Any],
        context: Any,
        can_use_tool: Any,
        parent_message: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        self._recorder.started.append(self.name)
        await asyncio.sleep(0.01)
        self._recorder.finished.append(self.name)
        return ToolResult(
            data={"tool": self.name},
            context_modifier=lambda ctx: type(ctx)(
                **{**ctx.__dict__, "tool_use_id": self.name}
            ),
        )


@pytest.mark.asyncio
async def test_streaming_tool_executor_runs_concurrency_safe_tools_together() -> None:
    recorder = _Recorder(started=[], finished=[])
    tools = [
        _ConcurrentReadTool(recorder, "read_a"),
        _ConcurrentReadTool(recorder, "read_b"),
    ]
    ctx = make_tool_use_context(tools=tools)
    executor = StreamingToolExecutor(tools, allow_all_can_use_tool, ctx)
    assistant = _assistant_with_tools(
        {"type": "tool_use", "id": "a", "name": "read_a", "input": {}},
        {"type": "tool_use", "id": "b", "name": "read_b", "input": {}},
    )
    executor.add_tool({"id": "a", "name": "read_a", "input": {}}, assistant)
    executor.add_tool({"id": "b", "name": "read_b", "input": {}}, assistant)
    results = [update async for update in executor.get_remaining_results()]
    assert len(results) == 2
    assert recorder.started == ["read_a", "read_b"]
    assert sorted(recorder.finished) == ["read_a", "read_b"]


@pytest.mark.asyncio
async def test_streaming_tool_executor_serializes_non_concurrency_safe_tools() -> None:
    recorder = _Recorder(started=[], finished=[])
    tools = [
        _ExclusiveWriteTool(recorder, "write_a"),
        _ConcurrentReadTool(recorder, "read_b"),
    ]
    ctx = make_tool_use_context(tools=tools)
    executor = StreamingToolExecutor(tools, allow_all_can_use_tool, ctx)
    assistant = _assistant_with_tools(
        {"type": "tool_use", "id": "a", "name": "write_a", "input": {}},
        {"type": "tool_use", "id": "b", "name": "read_b", "input": {}},
    )
    executor.add_tool({"id": "a", "name": "write_a", "input": {}}, assistant)
    executor.add_tool({"id": "b", "name": "read_b", "input": {}}, assistant)
    results = [update async for update in executor.get_remaining_results()]
    assert len(results) == 2
    assert recorder.started == ["write_a", "read_b"]
    assert recorder.finished == ["write_a", "read_b"]


@pytest.mark.asyncio
async def test_streaming_tool_executor_discard_suppresses_results() -> None:
    recorder = _Recorder(started=[], finished=[])
    tools = [_ConcurrentReadTool(recorder, "read_a")]
    ctx = make_tool_use_context(tools=tools)
    executor = StreamingToolExecutor(tools, allow_all_can_use_tool, ctx)
    assistant = _assistant_with_tools(
        {"type": "tool_use", "id": "a", "name": "read_a", "input": {}},
    )
    executor.add_tool({"id": "a", "name": "read_a", "input": {}}, assistant)
    executor.discard()
    results = [update async for update in executor.get_remaining_results()]
    assert results == []


@pytest.mark.asyncio
async def test_streaming_tool_executor_applies_context_modifiers_from_concurrent_tools() -> (
    None
):
    recorder = _Recorder(started=[], finished=[])
    tools = [_ContextModifyingReadTool(recorder, "read_a")]
    ctx = make_tool_use_context(tools=tools)
    executor = StreamingToolExecutor(tools, allow_all_can_use_tool, ctx)
    assistant = _assistant_with_tools(
        {"type": "tool_use", "id": "a", "name": "read_a", "input": {}},
    )
    executor.add_tool({"id": "a", "name": "read_a", "input": {}}, assistant)
    _ = [update async for update in executor.get_remaining_results()]
    assert executor.get_updated_context().tool_use_id == "read_a"
